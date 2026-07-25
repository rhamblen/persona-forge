"""Persona Forge API.

0.1.x proved the deploy loop + infrastructure checks.
0.2.x adds the Prompt Studio foundations: named projects (each backed by a build
folder), an append-only prompt version history with sign-off + rollback, and
generation through ComfyUI via workflow templates + manifests.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import comfy, db, docker_ctl, jobs, logs, ollama, workflows

COMFYUI_URL = comfy.COMFYUI_URL
BUILDS_ROOT = Path(os.getenv("BUILDS_ROOT", "/builds"))
# ComfyUI runs in a DIFFERENT container under a different user. Folders we create
# in the shared builds tree must therefore be owned/permissioned so ComfyUI can
# write into them, otherwise SaveImage fails with EACCES. Unraid default is
# nobody:users = 99:100.
BUILD_UID = int(os.getenv("PUID", "99"))
BUILD_GID = int(os.getenv("PGID", "100"))
DB_DIR = Path(os.getenv("DB_DIR", "/data/db"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/data/logs"))
def _resolve(name: str, must_be_dir: bool = True) -> Path:
    """Resolve a sibling asset in both layouts.

    Container: /app/app/main.py  -> /app/<name>
    Repo:      backend/app/main.py -> <repo-root>/<name>
    """
    here = Path(__file__).resolve()
    cands = [here.parent.parent / name, here.parent.parent.parent / name]
    for c in cands:
        if c.is_dir() if must_be_dir else c.is_file():
            return c
    return cands[0]


FRONTEND_DIR = _resolve("frontend")
_version_file = _resolve("VERSION", must_be_dir=False)
VERSION = _version_file.read_text().strip() if _version_file.is_file() else "0.0.0"

app = FastAPI(title="Persona Forge", version=VERSION)


@app.middleware("http")
async def _log_requests(request: Any, call_next: Any):
    """Every inbound request, at verbose — the firehose for tracing a flow end-to-end."""
    if request.url.path.startswith("/static"):
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000)
    logs.verbose("api", f"{request.method} {request.url.path} → {response.status_code}", ms=ms)
    return response


@app.on_event("startup")
async def _startup() -> None:
    logs.info("boot", f"Persona Forge {VERSION} starting")
    logs.info("boot", "config", comfyui_url=COMFYUI_URL, builds_root=str(BUILDS_ROOT),
              db_dir=str(DB_DIR), log_dir=str(LOG_DIR), frontend=str(FRONTEND_DIR))
    logs.verbose("boot", "environment",
                 ollama_url=ollama.OLLAMA_URL, ollama_model=ollama.OLLAMA_MODEL,
                 default_checkpoint=comfy.DEFAULT_CHECKPOINT,
                 docker_proxy=docker_ctl.DOCKER_PROXY_URL or "(disabled)",
                 puid=BUILD_UID, pgid=BUILD_GID)
    try:
        db.init_db()
        logs.info("boot", "database ready", path=str(db.DB_PATH))
    except Exception as exc:  # noqa: BLE001
        logs.error("boot", f"database init failed: {exc}")
        raise
    mounted = BUILDS_ROOT.is_dir()
    writable, err = (False, "not mounted") if not mounted else _probe_writable(BUILDS_ROOT)
    (logs.info if (mounted and writable) else logs.error)(
        "boot", "builds mount check", path=str(BUILDS_ROOT), mounted=mounted, writable=writable, error=err)
    for label, d in (("db", DB_DIR), ("logs", LOG_DIR)):
        if not d.is_dir():
            logs.warn("boot", f"{label} directory not mounted", path=str(d))
    wf = workflows.list_manifests()
    logs.info("boot", f"{len(wf)} workflow template(s) loaded",
              ids=[m.get("id") for m in wf], dir=str(workflows.WORKFLOW_DIR))
    for m in wf:
        probs = workflows.validate_manifest(m["id"]) if m.get("id") else []
        if probs:
            logs.warn("boot", f"workflow '{m.get('id')}' manifest problems", problems=probs)

    # Handshake with the external systems at boot so the log shows what's reachable.
    try:
        stats = await comfy.system_stats()
        ver = (stats.get("system") or {}).get("comfyui_version", "?")
        logs.info("integration", "ComfyUI reachable at boot", url=COMFYUI_URL, comfyui_version=ver)
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"ComfyUI not reachable at boot: {exc}", url=COMFYUI_URL)
    ost = await ollama.status()
    (logs.info if ost.get("reachable") else logs.warn)(
        "integration", f"Ollama {'reachable' if ost.get('reachable') else 'not reachable'} at boot",
        url=ollama.OLLAMA_URL, models=len(ost.get("models", [])))

    # The background job worker — advances builds (train → expressions) unattended,
    # independent of any open browser. Runs for the life of the container.
    asyncio.create_task(jobs.run_worker())

    logs.info("boot", "startup complete")


# --------------------------------------------------------------------------- #
# health / infrastructure
# --------------------------------------------------------------------------- #

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/api/comfyui/status")
async def comfyui_status() -> dict:
    started = time.perf_counter()
    try:
        stats = await comfy.system_stats()
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "url": COMFYUI_URL, "error": f"{type(exc).__name__}: {exc}"}

    system = stats.get("system", {}) or {}
    devices = stats.get("devices", []) or []
    gpu = devices[0] if devices else {}
    return {
        "connected": True,
        "url": COMFYUI_URL,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "comfyui_version": system.get("comfyui_version"),
        "python_version": (system.get("python_version") or "").split()[0] or None,
        "output_directory": _argv_value(system.get("argv", []), "--output-directory"),
        "gpu": gpu.get("name"),
        "vram_total_mb": round(gpu["vram_total"] / 1048576) if gpu.get("vram_total") else None,
        "vram_free_mb": round(gpu["vram_free"] / 1048576) if gpu.get("vram_free") else None,
    }


def _argv_value(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _share_with_comfyui(path: Path) -> None:
    """Make a folder we just created writable by the ComfyUI container."""
    try:
        os.chown(path, BUILD_UID, BUILD_GID)
    except Exception as exc:  # noqa: BLE001 - not root, or Windows
        logs.warn("local", f"chown failed on {path} ({exc}) — falling back to 0777")
        try:
            os.chmod(path, 0o777)
        except Exception as exc2:  # noqa: BLE001
            logs.error("local", f"chmod fallback failed on {path}: {exc2}")
        return
    try:
        os.chmod(path, 0o775)
    except Exception as exc:  # noqa: BLE001
        logs.warn("local", f"chmod failed on {path}: {exc}")


def _write_persona_sidecar(project_id: int) -> None:
    """Write persona.json into the build folder.

    The sqlite db in appdata/ is the working store, but that leaves a build folder
    non-self-describing: copy it elsewhere (or lose the db) and you keep the images
    but not the prompt that produced them. This sidecar makes each build portable
    and self-documenting. Best-effort — never break a request over it.
    """
    try:
        with db.connect() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if proj is None:
                return
            versions = conn.execute(
                "SELECT * FROM prompt_versions WHERE project_id = ? ORDER BY id", (project_id,)
            ).fetchall()
            current = conn.execute(
                "SELECT * FROM prompt_versions WHERE id = ?", (proj["current_version_id"],)
            ).fetchone()

        build_dir = BUILDS_ROOT / proj["slug"]
        if not build_dir.is_dir():
            return
        payload = {
            "persona": dict(proj),
            "current_version": db.row_to_dict(current),
            "signed_off_versions": [dict(v) for v in versions if v["signed_off"]],
            "version_history": [dict(v) for v in versions],
            "written_by": f"persona-forge {VERSION}",
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        target = build_dir / "persona.json"
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _share_with_comfyui(target)
    except Exception as exc:  # noqa: BLE001
        logs.warn("local", f"could not write persona.json: {exc}", project_id=project_id)


def _probe_writable(path: Path) -> tuple[bool, str | None]:
    probe = path / f".pf_write_probe_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("ok")
        probe.unlink()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


@app.get("/api/storage/status")
async def storage_status() -> dict:
    exists = BUILDS_ROOT.is_dir()
    writable, error = (False, "builds root not mounted") if not exists else _probe_writable(BUILDS_ROOT)
    return {
        "builds_root": str(BUILDS_ROOT),
        "mounted": exists,
        "writable": writable,
        "error": error,
        "db_dir": str(DB_DIR),
        "db_mounted": DB_DIR.is_dir(),
        "log_dir": str(LOG_DIR),
        "log_mounted": LOG_DIR.is_dir(),
    }


# --------------------------------------------------------------------------- #
# models + workflows
# --------------------------------------------------------------------------- #

@app.get("/api/models")
async def models(kind: str = "checkpoints") -> dict:
    try:
        models = await comfy.list_models(kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read models from ComfyUI: {exc}") from exc
    default = comfy.pick_default_checkpoint(models) if kind == "checkpoints" else ""
    return {"kind": kind, "models": models, "default": default}


@app.get("/api/workflows")
async def list_workflows() -> dict:
    return {"workflows": workflows.list_manifests()}


@app.get("/api/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict:
    try:
        return {
            "manifest": workflows.get_manifest(workflow_id),
            "defaults": workflows.defaults_for(workflow_id),
            "problems": workflows.validate_manifest(workflow_id),
        }
    except workflows.WorkflowError as exc:
        raise HTTPException(404, str(exc)) from exc


# --------------------------------------------------------------------------- #
# AI prompt assistant (Ollama)
# --------------------------------------------------------------------------- #

@app.get("/api/ai/status")
async def ai_status() -> dict:
    return await ollama.status()


@app.post("/api/ai/warm")
async def ai_warm() -> dict:
    """Preload the model so the next suggestion is instant (sidebar Connect)."""
    try:
        return await ollama.warm()
    except ollama.OllamaError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/ai/unload")
async def ai_unload() -> dict:
    """Free the model from VRAM now (sidebar Unload)."""
    try:
        return await ollama.unload()
    except ollama.OllamaError as exc:
        raise HTTPException(502, str(exc)) from exc


class AiSuggestRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    mode: str = "replace"  # 'replace' | 'modify'
    character: str = ""
    style: str = ""
    negative: str = ""
    model: str | None = None


@app.post("/api/ai/suggest-prompt")
async def ai_suggest_prompt(body: AiSuggestRequest) -> dict:
    """Turn a plain-language description into suggested character/style/negative text.

    Does NOT save anything — the UI shows the suggestion for accept/reject, and the
    existing version system records it only if the user saves a new version.
    """
    if body.mode not in ("replace", "modify"):
        raise HTTPException(422, "mode must be 'replace' or 'modify'")
    current = {"character": body.character, "style": body.style, "negative": body.negative}
    try:
        suggestion = await ollama.suggest_prompt(body.instruction, body.mode, current, body.model)
    except ollama.OllamaError as exc:
        raise HTTPException(502, f"AI assistant error: {exc}") from exc
    return {"mode": body.mode, "suggestion": suggestion}


# --------------------------------------------------------------------------- #
# container control (via the scoped docker-socket-proxy)
# --------------------------------------------------------------------------- #

@app.get("/api/containers/status")
async def containers_status() -> dict:
    return {
        "enabled": docker_ctl.enabled(),
        "containers": {key: await docker_ctl.state(key) for key in docker_ctl.CONTAINERS},
    }


@app.post("/api/containers/{key}/start")
async def container_start(key: str) -> dict:
    try:
        return await docker_ctl.start(key)
    except docker_ctl.DockerCtlError as exc:
        raise HTTPException(400 if "unknown" in str(exc) or "disabled" in str(exc) else 502,
                            str(exc)) from exc


@app.post("/api/containers/{key}/restart")
async def container_restart(key: str, force: bool = False) -> dict:
    """Restart a container. For ComfyUI this refuses while its queue is busy unless
    ?force=true, so we don't kill an in-flight generation by accident."""
    if key not in docker_ctl.CONTAINERS:
        raise HTTPException(400, f"unknown container key: {key}")
    if key == "comfyui" and not force:
        n = await comfy.queue_size()
        if n > 0:
            raise HTTPException(409, f"ComfyUI has {n} job(s) queued/running — "
                                     f"pass force=true to restart anyway")
    try:
        return await docker_ctl.restart(key)
    except docker_ctl.DockerCtlError as exc:
        raise HTTPException(400 if "disabled" in str(exc) else 502, str(exc)) from exc


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #

@app.get("/api/logs")
async def get_logs(level: str | None = None, category: str | None = None,
                   since_id: int = 0, limit: int = 300, search: str | None = None) -> dict:
    return {
        "levels": list(logs.LEVELS),
        "categories": list(logs.CATEGORIES),
        "entries": logs.read(level=level, category=category, since_id=since_id,
                             limit=limit, search=search),
        "stats": logs.stats(),
    }


@app.get("/api/logs/persisted")
async def get_persisted_logs(limit: int = 500) -> dict:
    """Log history from the rolling file — includes runs BEFORE this process."""
    return {"entries": logs.load_persisted(limit=limit)}


# --------------------------------------------------------------------------- #
# projects  (a project == a named build folder)
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(400, "project name must contain at least one alphanumeric character")
    return slug


def _default_negative() -> str:
    """The canonical starter negative prompt — read from the base-character template so
    there's one source of truth. New projects get this instead of a blank negative."""
    try:
        return (workflows.defaults_for("base-character").get("negative") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


@app.get("/api/prompt-defaults")
async def prompt_defaults() -> dict:
    """Starter values the Prompt Studio pre-fills for a fresh persona (e.g. the default
    negative prompt), so the negatives field isn't blank."""
    return {"negative": _default_negative()}


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    character: str = ""
    style: str = ""
    negative: str = ""
    checkpoint: str = ""
    seed: int = 123456789


@app.post("/api/projects", status_code=201)
async def create_project(body: ProjectCreate) -> dict:
    """Naming a project creates its build folder with lora/ + images/ subfolders."""
    slug = slugify(body.name)
    build_dir = BUILDS_ROOT / slug
    # An unset checkpoint used to be stored as '' and then shown as ComfyUI's
    # first entry (photoreal), so the first generate looked wrong. Resolve it now
    # so the initial version records a real, anime-first model.
    checkpoint = body.checkpoint or await comfy.default_checkpoint()
    # A fresh persona with no negatives renders low-quality junk; seed the canonical
    # starter negative when the caller didn't supply one (the user can edit it after).
    negative = (body.negative or "").strip() or _default_negative()

    if not BUILDS_ROOT.is_dir():
        raise HTTPException(503, f"builds root not mounted at {BUILDS_ROOT}")
    if build_dir.exists():
        raise HTTPException(409, f"a build folder named '{slug}' already exists")

    try:
        (build_dir / "lora").mkdir(parents=True)
        (build_dir / "images").mkdir(parents=True)
        # ComfyUI writes into images/ from its own container — align ownership
        for d in (build_dir, build_dir / "lora", build_dir / "images"):
            _share_with_comfyui(d)
    except OSError as exc:
        logs.error("local", f"could not create build folder: {exc}", path=str(build_dir))
        raise HTTPException(500, f"could not create build folder: {exc}") from exc
    logs.info("local", "build folder created", path=str(build_dir),
              subfolders=["lora", "images"], owner=f"{BUILD_UID}:{BUILD_GID}")

    with db.connect() as conn:
        cur = conn.execute("INSERT INTO projects (name, slug) VALUES (?, ?)", (body.name, slug))
        project_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO prompt_versions
               (project_id, parent_id, character, style, negative, checkpoint, seed, source, note)
               VALUES (?, NULL, ?, ?, ?, ?, ?, 'initial', 'initial version')""",
            (project_id, body.character, body.style, negative, checkpoint, body.seed),
        )
        conn.execute(
            "UPDATE projects SET current_version_id = ? WHERE id = ?", (cur.lastrowid, project_id)
        )

    logs.info("process", f"project created: {body.name}", project_id=project_id, slug=slug)
    _write_persona_sidecar(project_id)
    return await get_project(project_id)


@app.get("/api/projects")
async def list_projects() -> dict:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return {"projects": [dict(r) for r in rows]}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: int) -> dict:
    with db.connect() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        current = conn.execute(
            "SELECT * FROM prompt_versions WHERE id = ?", (proj["current_version_id"],)
        ).fetchone()
        n_versions = conn.execute(
            "SELECT COUNT(*) AS c FROM prompt_versions WHERE project_id = ?", (project_id,)
        ).fetchone()["c"]

    build_dir = BUILDS_ROOT / proj["slug"]
    return {
        "project": dict(proj),
        "current_version": db.row_to_dict(current),
        "version_count": n_versions,
        "build_dir": str(build_dir),
        "build_dir_exists": build_dir.is_dir(),
    }


# --------------------------------------------------------------------------- #
# prompt versions — append-only, with sign-off and rollback
# --------------------------------------------------------------------------- #

class VersionCreate(BaseModel):
    character: str | None = None
    style: str | None = None
    negative: str | None = None
    checkpoint: str | None = None
    seed: int | None = None
    source: str = "manual"          # manual | ollama
    note: str = ""


@app.get("/api/projects/{project_id}/versions")
async def list_versions(project_id: int) -> dict:
    with db.connect() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        rows = conn.execute(
            "SELECT * FROM prompt_versions WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    return {
        "versions": [dict(r) for r in rows],
        "current_version_id": proj["current_version_id"],
    }


@app.post("/api/projects/{project_id}/versions", status_code=201)
async def create_version(project_id: int, body: VersionCreate) -> dict:
    """An edit never mutates a row — it appends a child version and moves 'current'."""
    with db.connect() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        parent = conn.execute(
            "SELECT * FROM prompt_versions WHERE id = ?", (proj["current_version_id"],)
        ).fetchone()
        if parent is None:
            raise HTTPException(500, "project has no current version")

        merged = {
            "character": body.character if body.character is not None else parent["character"],
            "style": body.style if body.style is not None else parent["style"],
            "negative": body.negative if body.negative is not None else parent["negative"],
            "checkpoint": body.checkpoint if body.checkpoint is not None else parent["checkpoint"],
            "seed": body.seed if body.seed is not None else parent["seed"],
        }
        cur = conn.execute(
            """INSERT INTO prompt_versions
               (project_id, parent_id, character, style, negative, checkpoint, seed, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, parent["id"], merged["character"], merged["style"],
                merged["negative"], merged["checkpoint"], merged["seed"],
                body.source, body.note,
            ),
        )
        new_id = cur.lastrowid
        conn.execute("UPDATE projects SET current_version_id = ? WHERE id = ?", (new_id, project_id))
        row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (new_id,)).fetchone()
    logs.info("process", f"version v{new_id} created", project_id=project_id,
              parent=parent["id"], source=body.source, note=body.note)
    _write_persona_sidecar(project_id)
    return {"version": dict(row)}


@app.post("/api/versions/{version_id}/signoff")
async def sign_off(version_id: int) -> dict:
    """Pin a version as the approved baseline. Immutable and always restorable."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "version not found")
        conn.execute("UPDATE prompt_versions SET signed_off = 1 WHERE id = ?", (version_id,))
        row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,)).fetchone()
    logs.info("process", f"v{version_id} signed off as baseline", version_id=version_id)
    _write_persona_sidecar(row["project_id"])
    return {"version": dict(row)}


@app.post("/api/projects/{project_id}/rollback/{version_id}")
async def rollback(project_id: int, version_id: int) -> dict:
    """Roll 'current' back to any earlier version. Nothing is deleted."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE id = ? AND project_id = ?",
            (version_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "version not found for this project")
        conn.execute("UPDATE projects SET current_version_id = ? WHERE id = ?", (version_id, project_id))
    logs.info("process", f"rolled back to v{version_id}", project_id=project_id, version_id=version_id)
    _write_persona_sidecar(project_id)
    return await get_project(project_id)


class ProjectClone(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    style: str | None = None   # usually the whole point of a clone (outfit / scene)


@app.post("/api/projects/{project_id}/clone", status_code=201)
async def clone_project(project_id: int, body: ProjectClone) -> dict:
    """Clone a persona into a new project seeded with its current prompt.

    Use case: the same character, dressed/staged differently (skiing vs. beach).
    Identity lives in `character`, so a clone that only changes `style` is the same
    person — parent_project_id is recorded so Phase C can offer to reuse the
    parent's trained LoRA instead of retraining.
    """
    src = await get_project(project_id)
    v = src["current_version"] or {}
    slug = slugify(body.name)
    build_dir = BUILDS_ROOT / slug

    if not BUILDS_ROOT.is_dir():
        raise HTTPException(503, f"builds root not mounted at {BUILDS_ROOT}")
    if build_dir.exists():
        raise HTTPException(409, f"a build folder named '{slug}' already exists")

    try:
        (build_dir / "lora").mkdir(parents=True)
        (build_dir / "images").mkdir(parents=True)
        for d in (build_dir, build_dir / "lora", build_dir / "images"):
            _share_with_comfyui(d)
    except OSError as exc:
        raise HTTPException(500, f"could not create build folder: {exc}") from exc

    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, slug, parent_project_id) VALUES (?, ?, ?)",
            (body.name, slug, project_id),
        )
        new_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO prompt_versions
               (project_id, parent_id, character, style, negative, checkpoint, seed, source, note)
               VALUES (?, NULL, ?, ?, ?, ?, ?, 'initial', ?)""",
            (new_id, v.get("character", ""),
             body.style if body.style is not None else v.get("style", ""),
             v.get("negative", ""), v.get("checkpoint", ""), v.get("seed", 0),
             f"cloned from '{src['project']['name']}' (v{v.get('id')})"),
        )
        conn.execute("UPDATE projects SET current_version_id = ? WHERE id = ?", (cur.lastrowid, new_id))

    logs.info("process", f"persona cloned: {src['project']['name']} -> {body.name}",
              source_project_id=project_id, new_project_id=new_id, slug=slug,
              style_changed=body.style is not None)
    _write_persona_sidecar(new_id)
    return await get_project(new_id)


@app.post("/api/projects/{project_id}/repair-permissions")
async def repair_permissions(project_id: int) -> dict:
    """Re-apply ComfyUI-writable ownership to an existing build folder.

    Needed for folders created before 0.2.4, which came out root-owned and made
    ComfyUI's SaveImage fail with EACCES.
    """
    detail = await get_project(project_id)
    build_dir = Path(detail["build_dir"])
    if not build_dir.is_dir():
        raise HTTPException(404, f"build folder missing: {build_dir}")
    fixed = []
    for d in (build_dir, build_dir / "lora", build_dir / "images"):
        if d.is_dir():
            _share_with_comfyui(d)
            fixed.append(str(d))
    logs.info("local", "repaired build folder permissions", project_id=project_id, paths=fixed)
    return {"repaired": fixed, "owner": f"{BUILD_UID}:{BUILD_GID}"}


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #

class GenerateRequest(BaseModel):
    workflow: str = "base-character"
    params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = True


@app.post("/api/projects/{project_id}/generate")
async def generate(project_id: int, body: GenerateRequest) -> dict:
    """Run a workflow for this project, defaulting the prompt from its current version."""
    detail = await get_project(project_id)
    version = detail["current_version"] or {}
    slug = detail["project"]["slug"]

    params: dict[str, Any] = {
        "character": version.get("character") or None,
        "style": version.get("style") or None,
        "negative": version.get("negative") or None,
        "checkpoint": version.get("checkpoint") or None,
        "seed": version.get("seed"),
        "output_prefix": f"{slug}/images/preview",
    }
    params = {k: v for k, v in params.items() if v is not None}
    params.update(body.params)  # explicit params win

    logs.info("process", f"generation requested ({body.workflow})",
              project_id=project_id, slug=slug, version_id=version.get("id"))
    try:
        graph = workflows.build_graph(body.workflow, params)
        prompt_id = await comfy.submit(graph)
    except workflows.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    except comfy.ComfyError as exc:
        raise HTTPException(502, str(exc)) from exc

    if not body.wait:
        return {"prompt_id": prompt_id, "status": "queued"}

    try:
        entry = await comfy.wait(prompt_id)
    except comfy.ComfyError as exc:
        raise HTTPException(504, str(exc)) from exc

    err = comfy.error_message(entry)
    if err:
        logs.error("process", f"generation failed: {err}", project_id=project_id, prompt_id=prompt_id)
        raise HTTPException(502, f"ComfyUI execution error — {err}")

    images = comfy.outputs_from(entry)
    with db.connect() as conn:
        for img in images:
            conn.execute(
                """INSERT INTO images (project_id, version_id, filename, subfolder, kind)
                   VALUES (?, ?, ?, ?, 'preview')""",
                (project_id, version.get("id"), img["filename"], img["subfolder"]),
            )

    logs.info("process", f"generation complete — {len(images)} image(s)",
              project_id=project_id, prompt_id=prompt_id,
              files=[f"{i['subfolder']}/{i['filename']}" for i in images])
    return {
        "prompt_id": prompt_id,
        "status": "success",
        "images": [{**img, "url": comfy.view_url(img["filename"], img["subfolder"])} for img in images],
    }


@app.get("/api/image")
async def proxy_image(filename: str, subfolder: str = "", type: str = "output") -> Any:
    """Proxy ComfyUI images so the browser only ever talks to Persona Forge."""
    url = comfy.view_url(filename, subfolder, type)
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(r.status_code, "image not found")
    from fastapi.responses import Response

    return Response(content=r.content, media_type=r.headers.get("content-type", "image/png"))


@app.get("/api/builds")
async def list_builds() -> dict:
    if not BUILDS_ROOT.is_dir():
        return {"builds": [], "error": "builds root not mounted"}
    builds = []
    for entry in sorted(p for p in BUILDS_ROOT.iterdir() if p.is_dir()):
        images_dir = entry / "images"
        lora_dir = entry / "lora"
        builds.append(
            {
                "name": entry.name,
                "has_lora": lora_dir.is_dir(),
                "has_images": images_dir.is_dir(),
                "lora_count": len(list(lora_dir.glob("*.safetensors"))) if lora_dir.is_dir() else 0,
                "image_count": len(list(images_dir.glob("*.png"))) if images_dir.is_dir() else 0,
            }
        )
    return {"builds": builds}


# --------------------------------------------------------------------------- #
# dataset builder (Phase B)
# --------------------------------------------------------------------------- #

MAX_DATASET_BATCH = int(os.getenv("MAX_DATASET_BATCH", "60"))

# A LoRA only learns identity *independent of stance and expression* if the training set
# shows the character across many framings, poses AND facial expressions. Left to vary only
# the seed, every candidate is the same waist-up neutral stance, so the LoRA overfits it and
# then (a) can't do other poses, (b) has a weak face (the face is a tiny patch in a full-body
# frame), and (c) glues one expression into identity. We fix all three by cycling candidates
# across two axes — framing and expression — combining each pair into the base-character
# `expression` suffix (node 5), the same already-validated lever the Poses tab uses. Because
# the trainer captions every image (Florence-2), the varied expressions land in the caption
# and decouple from the trigger word rather than binding to identity.
#
# Framings are split into two pools so a batch can target one axis (see `mode` below):
#   - FACE: close-up/bust shots at varied head angles — carry the high-frequency identity
#     detail (a set with too few of these gives a weak, blurry face) and are where expressions
#     read clearly.
#   - BODY: full-body poses + many views/angles AROUND the body — teach proportions, outfit and
#     pose independence so the LoRA isn't stance-locked.
# Keep entries short and non-conflicting so they win cleanly over any framing baked into the
# style prompt.
DATASET_FACE_FRAMINGS = [
    "close-up portrait, face fills the frame, front view, looking at the viewer",
    "close-up portrait, three-quarter view from the left",
    "close-up portrait, three-quarter view from the right",
    "close-up portrait, side profile",
    "close-up, looking slightly up, chin raised",
    "close-up, looking slightly down",
    "close-up portrait, looking over the shoulder",
    "bust shot, head and shoulders, facing the viewer",
    "bust shot, head and shoulders, three-quarter view",
]

DATASET_BODY_FRAMINGS = [
    "full body, standing, front view, relaxed natural pose",
    "full body, standing, back view, seen from behind",
    "full body, standing, left side view",
    "full body, standing, right side view",
    "full body, three-quarter view from the front",
    "full body, three-quarter view from behind, looking over the shoulder",
    "full body, from a low angle, standing",
    "full body, from a high angle, looking down",
    "full body, walking forward, mid-stride",
    "full body, walking away, seen from behind",
    "full body, running, dynamic action pose",
    "full body, sitting on the floor, relaxed",
    "full body, sitting on a chair, legs crossed",
    "full body, kneeling",
    "full body, crouching",
    "full body, leaning against a wall, casual",
    "full body, arms crossed, confident stance",
    "full body, hands on hips",
    "full body, arms raised, stretching",
    "full body, jumping, mid-air, dynamic",
    "cowboy shot, from the thighs up, front view",
    "cowboy shot, from the thighs up, three-quarter view",
    "full body, twisting at the waist, turning",
    "full body, one hand waving, the other relaxed",
]

# Expressions: neutral-weighted with a wide spread so no single expression binds to identity.
# "Alluring"/"flirtatious" are kept at a tasteful expression (gaze/smirk) level. Tune freely.
DATASET_EXPRESSIONS = [
    "neutral expression, relaxed",
    "neutral expression, calm, closed mouth",
    "gentle happy smile",
    "bright cheerful smile, laughing",
    "soft warm smile",
    "sad, downcast eyes",
    "crying, teary eyes",
    "angry, furrowed brow",
    "annoyed, frowning",
    "shocked, wide eyes, open mouth",
    "surprised, raised eyebrows",
    "embarrassed, blushing, shy",
    "nervous, worried look",
    "thoughtful, looking away",
    "pouting, sulking",
    "confident smirk",
    "alluring, soft half-lidded gaze",
    "flirtatious, playful smirk",
]

# On full-body shots the face is tiny, so keep expressions light there — the emphasis is the
# pose/view, not the face.
DATASET_POSE_EXPRESSIONS = [
    "neutral expression",
    "relaxed",
    "gentle smile",
    "confident",
]


def _dataset_variation(n: int, mode: str = "both") -> str:
    """A framing+expression suffix for candidate index `n`, per `mode`:
      - "faces": close-up/bust framings × the full expression spread (top up a weak face).
      - "poses": full-body framings + many views × light expressions (top up weak poses/angles).
      - "both" : alternate a face shot and a body shot (≈50/50), with full expression variety.
    Framing and expression rotate at different rates so pairs vary and don't repeat quickly."""
    if mode == "faces":
        framing = DATASET_FACE_FRAMINGS[n % len(DATASET_FACE_FRAMINGS)]
        return f"{framing}, {DATASET_EXPRESSIONS[n % len(DATASET_EXPRESSIONS)]}"
    if mode == "poses":
        framing = DATASET_BODY_FRAMINGS[n % len(DATASET_BODY_FRAMINGS)]
        return f"{framing}, {DATASET_POSE_EXPRESSIONS[n % len(DATASET_POSE_EXPRESSIONS)]}"
    # both — alternate face/body so every batch keeps a strong share of close-ups
    if n % 2 == 0:
        framing = DATASET_FACE_FRAMINGS[(n // 2) % len(DATASET_FACE_FRAMINGS)]
    else:
        framing = DATASET_BODY_FRAMINGS[(n // 2) % len(DATASET_BODY_FRAMINGS)]
    return f"{framing}, {DATASET_EXPRESSIONS[n % len(DATASET_EXPRESSIONS)]}"


def _version_prompt_params(version: dict[str, Any]) -> dict[str, Any]:
    """The prompt fields shared by preview + dataset generation (seed added by caller)."""
    params = {
        "character": version.get("character") or None,
        "style": version.get("style") or None,
        "negative": version.get("negative") or None,
        "checkpoint": version.get("checkpoint") or None,
    }
    return {k: v for k, v in params.items() if v is not None}


async def _reconcile_dataset(project_id: int) -> None:
    """Pull finished queued images into the images table as kind='dataset'."""
    with db.connect() as conn:
        pending = conn.execute(
            "SELECT id, prompt_id FROM dataset_jobs WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        ).fetchall()
    if not pending:
        return
    logs.verbose("process", f"reconciling {len(pending)} pending dataset job(s) against history",
                 project_id=project_id)
    try:
        hist = await comfy.history_all()
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not read ComfyUI history to reconcile dataset: {exc}")
        return
    done, failed, still = 0, 0, 0
    with db.connect() as conn:
        for job in pending:
            entry = hist.get(job["prompt_id"])
            if not entry:
                still += 1
                continue  # still queued/running, or aged out of history
            st = comfy.status_of(entry)
            if st == "success":
                imgs = comfy.outputs_from(entry)
                for img in imgs:
                    conn.execute(
                        """INSERT INTO images (project_id, version_id, filename, subfolder, kind)
                           VALUES (?, NULL, ?, ?, 'dataset')""",
                        (project_id, img["filename"], img["subfolder"]),
                    )
                conn.execute("UPDATE dataset_jobs SET status = 'done' WHERE id = ?", (job["id"],))
                done += 1
                logs.verbose("process", "dataset image finished",
                             prompt_id=job["prompt_id"], files=[i["filename"] for i in imgs])
            elif st == "error":
                conn.execute("UPDATE dataset_jobs SET status = 'error' WHERE id = ?", (job["id"],))
                failed += 1
                logs.warn("process", "a dataset image failed to render", prompt_id=job["prompt_id"])
    if done or failed:
        logs.info("process", f"dataset reconcile: {done} finished, {failed} failed, {still} still running",
                  project_id=project_id)


class DatasetGenerateRequest(BaseModel):
    count: int = 30
    # Spread candidates across framing × expression so the training set has framing + pose +
    # expression variety (the fix for weak, pose-locked LoRAs). Turn off for a same-framing,
    # neutral, seed-only batch.
    pose_variety: bool = True
    # Which axis to spread across — "both" (faces + poses), "faces" (close-ups + expressions,
    # to strengthen a weak face) or "poses" (full body + many views, to strengthen weak poses).
    mode: Literal["both", "faces", "poses"] = "both"


@app.post("/api/projects/{project_id}/dataset/generate")
async def dataset_generate(project_id: int, body: DatasetGenerateRequest) -> dict:
    """Queue `count` candidate images from the current prompt.

    With `pose_variety` on (default) each candidate is drawn from a different framing *and*
    facial expression (plus a fresh seed), so the resulting training set teaches identity
    independent of stance and expression, with enough close-ups for a strong face. With it
    off, only the seed varies (the original behaviour).
    """
    detail = await get_project(project_id)
    version = detail["current_version"] or {}
    slug = detail["project"]["slug"]
    count = max(1, min(body.count, MAX_DATASET_BATCH))
    base = _version_prompt_params(version)
    vary = body.pose_variety

    # Continue the variation rotation across successive batches (Generate 30, then +10 more)
    # so coverage stays even instead of restarting at the first framing each time.
    offset = 0
    if vary:
        with db.connect() as conn:
            offset = conn.execute(
                "SELECT COUNT(*) n FROM dataset_jobs WHERE project_id = ?", (project_id,),
            ).fetchone()["n"]

    mode_desc = {"both": "faces + poses", "faces": "close-up faces + expressions",
                 "poses": "full body + many views"}.get(body.mode, body.mode)
    logs.info("process",
              f"dataset: queuing a batch of {count} "
              + (f"across varied {mode_desc}" if vary else "at fresh seeds (same framing/expression)"),
              project_id=project_id, slug=slug)
    queued = 0
    with db.connect() as conn:
        for i in range(count):
            seed = random.randint(1, 2**31 - 1)
            params = {**base, "seed": seed, "output_prefix": f"{slug}/images/ds"}
            variation = None
            if vary:
                variation = _dataset_variation(offset + i, body.mode)
                params["expression"] = variation
            try:
                graph = workflows.build_graph("base-character", params)
                logs.verbose("process", f"queuing dataset image {i + 1}/{count}",
                             seed=seed, variation=variation)
                prompt_id = await comfy.submit(graph)
            except (workflows.WorkflowError, comfy.ComfyError) as exc:
                # stop the batch but keep whatever already queued
                logs.error("process", f"dataset generate failed after {queued}: {exc}",
                           project_id=project_id)
                if queued == 0:
                    raise HTTPException(502, f"could not queue dataset: {exc}") from exc
                break
            conn.execute(
                "INSERT INTO dataset_jobs (project_id, prompt_id, status) VALUES (?, ?, 'pending')",
                (project_id, prompt_id),
            )
            queued += 1

    logs.info("process", f"dataset: queued {queued} image(s)", project_id=project_id, slug=slug)
    return {"queued": queued, "pose_variety": vary, "mode": body.mode if vary else None}


@app.get("/api/projects/{project_id}/dataset")
async def dataset_list(project_id: int) -> dict:
    await _reconcile_dataset(project_id)
    with db.connect() as conn:
        proj = conn.execute("SELECT dataset_target FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        rows = conn.execute(
            """SELECT id, filename, subfolder, selected FROM images
               WHERE project_id = ? AND kind = 'dataset' ORDER BY id""",
            (project_id,),
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) n FROM dataset_jobs WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        ).fetchone()["n"]
    images = [dict(r) for r in rows]
    selected = sum(1 for r in images if r["selected"])
    target = proj["dataset_target"]
    return {
        "target": target,
        "generating": pending > 0,
        "counts": {"candidates": len(images), "selected": selected, "pending": pending},
        "reached": selected >= target,
        "images": images,
    }


class DatasetSelectRequest(BaseModel):
    image_id: int
    selected: bool


@app.post("/api/projects/{project_id}/dataset/select")
async def dataset_select(project_id: int, body: DatasetSelectRequest) -> dict:
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE images SET selected = ? WHERE id = ? AND project_id = ? AND kind = 'dataset'",
            (1 if body.selected else 0, body.image_id, project_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "dataset image not found")
    return {"image_id": body.image_id, "selected": body.selected}


class DatasetTargetRequest(BaseModel):
    target: int = Field(ge=1, le=200)


@app.post("/api/projects/{project_id}/dataset/target")
async def dataset_target(project_id: int, body: DatasetTargetRequest) -> dict:
    with db.connect() as conn:
        cur = conn.execute("UPDATE projects SET dataset_target = ? WHERE id = ?",
                           (body.target, project_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "project not found")
    return {"target": body.target}


def _delete_dataset_file(subfolder: str, filename: str) -> bool:
    """Delete one dataset image file from /builds. Guards against a crafted subfolder/filename
    escaping the builds root before unlinking. Best-effort — returns True if a file was removed."""
    if not filename:
        return False
    path = (BUILDS_ROOT / (subfolder or "") / filename).resolve()
    try:
        path.relative_to(BUILDS_ROOT.resolve())  # refuse anything outside /builds
    except ValueError:
        logs.warn("local", "refusing to delete outside the builds root", path=str(path))
        return False
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError as exc:
        logs.warn("local", f"could not delete dataset file: {exc}", path=str(path))
    return False


@app.post("/api/projects/{project_id}/dataset/purge")
async def dataset_purge(project_id: int) -> dict:
    """Delete every UNSELECTED dataset candidate for a project — both the DB rows and the files
    on /builds. Selected images (the training set) are left untouched. Irreversible."""
    with db.connect() as conn:
        proj = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        rows = conn.execute(
            """SELECT id, filename, subfolder FROM images
               WHERE project_id = ? AND kind = 'dataset' AND selected = 0""",
            (project_id,),
        ).fetchall()
    files_removed = sum(_delete_dataset_file(r["subfolder"], r["filename"]) for r in rows)
    with db.connect() as conn:
        deleted = conn.execute(
            "DELETE FROM images WHERE project_id = ? AND kind = 'dataset' AND selected = 0",
            (project_id,),
        ).rowcount
    logs.info("process",
              f"dataset purge: removed {deleted} unselected candidate(s), {files_removed} file(s)",
              project_id=project_id, slug=proj["slug"])
    return {"deleted": deleted, "files_removed": files_removed}


@app.delete("/api/projects/{project_id}/dataset/{image_id}")
async def dataset_delete(project_id: int, image_id: int) -> dict:
    """Delete one dataset candidate (DB row + file on /builds), selected or not. Irreversible."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT filename, subfolder FROM images
               WHERE id = ? AND project_id = ? AND kind = 'dataset'""",
            (image_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "dataset image not found")
        conn.execute("DELETE FROM images WHERE id = ? AND project_id = ?", (image_id, project_id))
    removed = _delete_dataset_file(row["subfolder"], row["filename"])
    logs.info("process", f"dataset: deleted candidate {image_id} (file removed: {removed})",
              project_id=project_id)
    return {"deleted": image_id, "file_removed": removed}


# --------------------------------------------------------------------------- #
# LoRA trainer (Phase C) — 0.5.0: trigger word + stage dataset into ComfyUI input
# --------------------------------------------------------------------------- #

_TRIGGER_RE = re.compile(r"[^a-z0-9]+")


def default_trigger(slug: str) -> str:
    """A token-safe trigger word derived from the slug, e.g. 'pf_ada_blonde'."""
    return "pf_" + _TRIGGER_RE.sub("_", slug.lower()).strip("_")


def _fmt_dur(seconds: float) -> str:
    """Human duration for logs/UI: 95 -> '1m35s', 24 -> '24s', 3800 -> '1h3m20s'."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def _input_folder(slug: str) -> str:
    return f"pf-{slug}"


async def _dataset_folder_exists(folder: str) -> bool:
    """Is `folder` visible to ComfyUI's dataset loader (i.e. staged into input/)?"""
    try:
        info = await comfy.object_info("LoadImageTextDataSetFromFolder")
        opts = info["LoadImageTextDataSetFromFolder"]["input"]["required"]["folder"][1]["options"]
        return folder in opts
    except Exception:  # noqa: BLE001
        return False


async def _reconcile_training(project_id: int) -> None:
    """Flip a project's train_status once its ComfyUI training prompt finishes — and heal an
    ORPHANED 'training' flag left behind by a stopped/failed build. Without this, a build that
    was cancelled mid-training leaves train_status='training' forever, and every future build
    dies with 'a training run is already in progress for this persona'.

    Staleness is judged from ComfyUI reality, not from job rows, so it heals correctly even when
    called from inside the very build that's trying to start: a real run ALWAYS has a
    train_prompt_id (set together with the flag), so a null prompt id is unconditionally stale;
    a set-but-vanished prompt is stale only once ComfyUI's queue is idle."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT train_prompt_id, train_status, train_started_at, train_steps "
            "FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if not row or row["train_status"] != "training":
        return

    def _heal(reason: str) -> None:
        with db.connect() as conn:
            conn.execute("UPDATE projects SET train_status = 'error' WHERE id = ?", (project_id,))
        logs.warn("process", f"cleared an orphaned 'training' flag ({reason})", project_id=project_id)

    # No prompt id → can never have been a live run. Always stale.
    if not row["train_prompt_id"]:
        _heal("no prompt id")
        return
    try:
        hist = await comfy.history_all()
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not read history to reconcile training: {exc}")
        return
    entry = hist.get(row["train_prompt_id"])
    if not entry:
        # Prompt gone from history. If ComfyUI's queue is idle, the run is over
        # (interrupted / aged out) and the flag is stale. If the queue is busy we can't be sure
        # it isn't ours still running — leave it.
        if await comfy.queue_size() == 0:
            _heal("prompt gone, queue idle")
        return  # still training, or aged out mid-run
    st = comfy.status_of(entry)
    if st == "success":
        started = row["train_started_at"] or 0
        steps = row["train_steps"] or 0
        duration = max(0.0, time.time() - started) if started else 0.0
        with db.connect() as conn:
            if duration > 0:
                conn.execute(
                    "UPDATE projects SET train_status = 'done', last_train_seconds = ?, "
                    "last_train_steps = ? WHERE id = ?", (duration, steps, project_id))
            else:
                conn.execute("UPDATE projects SET train_status = 'done' WHERE id = ?", (project_id,))
        if duration > 0:
            per_step = f", {duration / steps:.1f}s/step" if steps else ""
            logs.info("process",
                      f"LoRA training finished in {_fmt_dur(duration)} ({steps} steps{per_step})",
                      project_id=project_id, prompt_id=row["train_prompt_id"],
                      duration_seconds=round(duration, 1), steps=steps)
        else:
            logs.info("process", "LoRA training finished", project_id=project_id,
                      prompt_id=row["train_prompt_id"])
    elif st == "error":
        with db.connect() as conn:
            conn.execute("UPDATE projects SET train_status = 'error' WHERE id = ?", (project_id,))
        logs.error("process", f"LoRA training failed — {comfy.error_message(entry)}",
                   project_id=project_id, prompt_id=row["train_prompt_id"])


@app.get("/api/projects/{project_id}/lora")
async def lora_status(project_id: int) -> dict:
    await _reconcile_training(project_id)
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    with db.connect() as conn:
        proj = conn.execute(
            "SELECT trigger_word, train_status, train_started_at, train_steps, "
            "last_train_seconds, last_train_steps FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        selected = conn.execute(
            "SELECT COUNT(*) n FROM images WHERE project_id = ? AND kind = 'dataset' AND selected = 1",
            (project_id,),
        ).fetchone()["n"]
    trigger = (proj["trigger_word"] if proj else "") or default_trigger(slug)
    folder = _input_folder(slug)
    lora_dir = BUILDS_ROOT / slug / "lora"
    loras = sorted(p.name for p in lora_dir.glob("*.safetensors")) if lora_dir.is_dir() else []

    status = proj["train_status"] if proj else "none"
    last_secs = (proj["last_train_seconds"] if proj else 0) or 0
    last_steps = (proj["last_train_steps"] if proj else 0) or 0
    elapsed = eta = remaining = None
    if status == "training" and proj and proj["train_started_at"]:
        elapsed = max(0.0, time.time() - proj["train_started_at"])
        if last_secs > 0:                       # ETA from the previous completed run
            eta = last_secs
            remaining = max(0.0, last_secs - elapsed)
    return {
        "trigger_word": trigger,
        "selected_count": selected,
        "input_folder": folder,
        "staged": await _dataset_folder_exists(folder),
        "loras": loras,
        "train_status": status,
        "current_steps": (proj["train_steps"] if proj else 0) or 0,
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "eta_seconds": round(eta, 1) if eta is not None else None,
        "remaining_seconds": round(remaining, 1) if remaining is not None else None,
        "last_train_seconds": round(last_secs, 1) if last_secs else None,
        "last_train_steps": last_steps or None,
    }


class LoraTrainRequest(BaseModel):
    steps: int = Field(default=500, ge=1, le=5000)
    rank: int = Field(default=16, ge=1, le=128)
    learning_rate: float = Field(default=0.0005, gt=0, le=0.1)


class TrainNotReady(RuntimeError):
    """Precondition for training not met (already running, dataset not staged)."""


async def _start_lora_training(project_id: int, steps: int, rank: int,
                               learning_rate: float) -> dict:
    """Kick off one LoRA training run and mark the project `training`.

    Shared by the manual endpoint and the `lora_build` job handler. Raises TrainNotReady
    for precondition failures and workflows.WorkflowError / comfy.ComfyError for submit
    failures — the callers map those to HTTP or job errors.
    """
    detail = await get_project(project_id)
    version = detail["current_version"] or {}
    slug = detail["project"]["slug"]
    folder = _input_folder(slug)
    # Heal a stale 'training' flag (from a stopped/failed build) before the gate, so a rebuild
    # isn't blocked by a run that isn't actually happening.
    await _reconcile_training(project_id)
    with db.connect() as conn:
        proj = conn.execute(
            "SELECT trigger_word, train_status, last_train_seconds, last_train_steps "
            "FROM projects WHERE id = ?", (project_id,)).fetchone()
    if proj and proj["train_status"] == "training":
        raise TrainNotReady("a training run is already in progress for this persona")
    if not await _dataset_folder_exists(folder):
        raise TrainNotReady("dataset not staged — use 'Stage dataset' first")
    trigger = (proj["trigger_word"] if proj else "") or default_trigger(slug)
    checkpoint = version.get("checkpoint") or comfy.DEFAULT_CHECKPOINT

    # Training needs VRAM headroom (an OOM here is the #1 failure). Free ComfyUI's
    # models and unload the Ollama model first.
    logs.info("process", "preparing to train LoRA — freeing VRAM",
              project_id=project_id, trigger=trigger, steps=steps, rank=rank)
    try:
        await ollama.unload()
    except ollama.OllamaError:
        pass
    await comfy.free_memory()

    params = {
        "checkpoint": checkpoint,
        "dataset_folder": folder,
        "trigger": trigger,
        "steps": steps,
        "rank": rank,
        "learning_rate": learning_rate,
        "seed": version.get("seed") or 0,
        "output_prefix": f"{slug}/lora/{trigger}",
    }
    graph = workflows.build_graph("lora-train", params)
    prompt_id = await comfy.submit(graph)

    started_at = time.time()
    with db.connect() as conn:
        conn.execute(
            "UPDATE projects SET train_prompt_id = ?, train_status = 'training', "
            "train_started_at = ?, train_steps = ? WHERE id = ?",
            (prompt_id, started_at, steps, project_id))

    prev_secs = (proj["last_train_seconds"] if proj else 0) or 0
    prev_steps = (proj["last_train_steps"] if proj else 0) or 0
    eta_note = ""
    if prev_secs > 0:
        eta_note = (f" — previous run took {_fmt_dur(prev_secs)}"
                    + (f" for {prev_steps} steps" if prev_steps else "")
                    + f", so ~{_fmt_dur(prev_secs)} ETA")
    logs.info("process",
              f"LoRA training started at {datetime.now().strftime('%H:%M:%S')} "
              f"({steps} steps, rank {rank}){eta_note}",
              project_id=project_id, prompt_id=prompt_id, checkpoint=checkpoint,
              started_at=started_at, prev_train_seconds=round(prev_secs, 1) or None)
    return {"status": "training", "prompt_id": prompt_id, "trigger_word": trigger,
            "steps": steps, "rank": rank,
            "eta_seconds": prev_secs or None, "prev_steps": prev_steps or None}


@app.post("/api/projects/{project_id}/lora/train")
async def lora_train(project_id: int, body: LoraTrainRequest) -> dict:
    """Train the character LoRA from the staged dataset (native ComfyUI TrainLoraNode)."""
    try:
        return await _start_lora_training(project_id, body.steps, body.rank, body.learning_rate)
    except TrainNotReady as exc:
        code = 409 if "already in progress" in str(exc) else 400
        raise HTTPException(code, str(exc)) from exc
    except workflows.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    except comfy.ComfyError as exc:
        raise HTTPException(502, str(exc)) from exc


class LoraTriggerRequest(BaseModel):
    trigger_word: str = Field(min_length=1, max_length=64)


@app.post("/api/projects/{project_id}/lora/trigger")
async def lora_set_trigger(project_id: int, body: LoraTriggerRequest) -> dict:
    trigger = _TRIGGER_RE.sub("_", body.trigger_word.lower()).strip("_")
    if not trigger:
        raise HTTPException(422, "trigger word must contain a letter or digit")
    with db.connect() as conn:
        cur = conn.execute("UPDATE projects SET trigger_word = ? WHERE id = ?", (trigger, project_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "project not found")
    return {"trigger_word": trigger}


@app.post("/api/projects/{project_id}/lora/stage")
async def lora_stage(project_id: int) -> dict:
    """Push the selected dataset images into ComfyUI's input/pf-<slug> over HTTP."""
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    folder = _input_folder(slug)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT filename, subfolder FROM images
               WHERE project_id = ? AND kind = 'dataset' AND selected = 1 ORDER BY id""",
            (project_id,),
        ).fetchall()
    if not rows:
        logs.warn("process", "stage requested but no images are selected", project_id=project_id)
        raise HTTPException(400, "no images selected — pick some in the Dataset tab first")

    logs.info("process", f"staging {len(rows)} selected image(s): /builds → ComfyUI input/{folder}",
              project_id=project_id, source=str(BUILDS_ROOT / slug / "images"))
    staged, errors = 0, []
    for r in rows:
        src = BUILDS_ROOT / r["subfolder"] / r["filename"]
        try:
            logs.verbose("local", "reading dataset image from builds share", path=str(src))
            data = src.read_bytes()
            logs.verbose("local", "read image bytes", filename=r["filename"], bytes=len(data))
            await comfy.upload_image(data, r["filename"], folder)  # logs its own handshake
            staged += 1
        except FileNotFoundError:
            logs.warn("local", "selected image is missing on the builds share", path=str(src))
            errors.append(f"{r['filename']}: not found on /builds")
        except Exception as exc:  # noqa: BLE001
            logs.error("integration", f"failed to stage {r['filename']}: {exc}", path=str(src))
            errors.append(f"{r['filename']}: {exc}")
    logs.info("process", f"staged {staged}/{len(rows)} image(s) to ComfyUI input/{folder}",
              project_id=project_id, staged=staged, failed=len(errors))
    if staged == 0:
        raise HTTPException(502, f"could not stage any images: {errors[:3]}")
    return {"staged": staged, "total": len(rows), "input_folder": folder, "errors": errors[:5]}


# --------------------------------------------------------------------------- #
# pose / expression studio (Phase D)
# --------------------------------------------------------------------------- #

STARTER_POSES = [
    ("Full body", "full body shot, standing, relaxed natural pose"),
    ("Portrait", "close-up portrait, head and shoulders"),
    ("Three-quarter", "three-quarter view, upper body"),
    ("Sitting", "sitting down, relaxed, hands in lap"),
    ("Side profile", "side profile view, looking to the side"),
    ("Waving", "waving hello, friendly, one hand raised"),
    ("Arms crossed", "arms crossed, confident stance"),
    ("Walking", "walking forward, mid-stride, dynamic"),
]

# The 28 SillyTavern expression sprites (the export target).
EXPRESSIONS_28 = [
    "admiration", "amusement", "anger", "annoyance", "approval", "caring", "confusion",
    "curiosity", "desire", "disappointment", "disapproval", "disgust", "embarrassment",
    "excitement", "fear", "gratitude", "grief", "joy", "love", "nervousness", "neutral",
    "optimism", "pride", "realization", "relief", "remorse", "sadness", "surprise",
]

PRESETS = {
    "starter": STARTER_POSES,
    "expressions": [(e.capitalize(), f"{e} facial expression") for e in EXPRESSIONS_28],
}


def _pose_dict(row: Any) -> dict:
    d = dict(row)
    return d


async def _reconcile_poses(project_id: int) -> None:
    with db.connect() as conn:
        pending = conn.execute(
            "SELECT id, prompt_id FROM poses WHERE project_id = ? AND status = 'pending' AND prompt_id != ''",
            (project_id,),
        ).fetchall()
    if not pending:
        return
    logs.verbose("process", f"reconciling {len(pending)} pending pose render(s)", project_id=project_id)
    try:
        hist = await comfy.history_all()
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not read history to reconcile poses: {exc}")
        return
    done = failed = 0
    with db.connect() as conn:
        for job in pending:
            entry = hist.get(job["prompt_id"])
            if not entry:
                continue
            st = comfy.status_of(entry)
            if st == "success":
                imgs = comfy.outputs_from(entry)
                if imgs:
                    conn.execute(
                        "UPDATE poses SET filename = ?, subfolder = ?, status = 'done' WHERE id = ?",
                        (imgs[-1]["filename"], imgs[-1]["subfolder"], job["id"]),
                    )
                    done += 1
                    logs.verbose("process", "pose render finished", pose_id=job["id"],
                                 file=imgs[-1]["filename"])
            elif st == "error":
                conn.execute("UPDATE poses SET status = 'error' WHERE id = ?", (job["id"],))
                failed += 1
                logs.warn("process", "a pose render failed", pose_id=job["id"])
    if done or failed:
        logs.info("process", f"pose reconcile: {done} finished, {failed} failed", project_id=project_id)


def _pose_lora_cfg(conn, project_id: int, slug: str) -> dict[str, Any] | None:
    """Resolve the project's selected pose LoRA into build_graph params, or None.

    Returns None (→ render LoRA-free via base-character) when no LoRA is selected or
    the selected file has since vanished from disk.
    """
    row = conn.execute(
        "SELECT trigger_word, pose_lora, pose_lora_strength FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    name = (row["pose_lora"] or "").strip()
    if not name:
        return None
    if not (BUILDS_ROOT / slug / "lora" / name).is_file():
        logs.warn("process", f"selected pose LoRA '{name}' is not on disk — rendering without it",
                  project_id=project_id)
        return None
    trigger = (row["trigger_word"] or "") or default_trigger(slug)
    return {
        "lora_name": f"{slug}/lora/{name}",       # relative to extra_model_paths loras root (/builds)
        "lora_strength": row["pose_lora_strength"] or 1.0,
        "trigger": trigger,
    }


async def _queue_pose(conn, project_id: int, slug: str, version: dict, pose_row: Any,
                      lora_cfg: dict | None = None) -> None:
    """Submit one pose's render and mark it pending.

    With a resolved `lora_cfg` the render goes through `pose-with-lora` (character LoRA
    loaded, trigger prepended); otherwise it uses the LoRA-free `base-character` graph.
    """
    params = {
        **_version_prompt_params(version),
        "seed": version.get("seed") or 0,
        "expression": pose_row["modifier"] or None,
        "output_prefix": f"{slug}/images/pose_{pose_row['id']}",
    }
    if lora_cfg:
        workflow_id = "pose-with-lora"
        params["lora_name"] = lora_cfg["lora_name"]
        params["lora_strength"] = lora_cfg["lora_strength"]
        params["trigger"] = lora_cfg["trigger"]
    else:
        workflow_id = "base-character"
    params = {k: v for k, v in params.items() if v is not None}
    logs.verbose("process", "queuing pose render", pose_id=pose_row["id"], name=pose_row["name"],
                 workflow=workflow_id, lora=lora_cfg["lora_name"] if lora_cfg else None)
    graph = workflows.build_graph(workflow_id, params)
    prompt_id = await comfy.submit(graph)
    conn.execute("UPDATE poses SET prompt_id = ?, status = 'pending' WHERE id = ?",
                 (prompt_id, pose_row["id"]))


@app.get("/api/projects/{project_id}/poses")
async def poses_list(project_id: int) -> dict:
    await _reconcile_poses(project_id)
    with db.connect() as conn:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        rows = conn.execute(
            "SELECT * FROM poses WHERE project_id = ? ORDER BY position, id", (project_id,)
        ).fetchall()
    poses = [_pose_dict(r) for r in rows]
    pending = sum(1 for p in poses if p["status"] == "pending")
    return {"poses": poses, "generating": pending > 0,
            "counts": {"total": len(poses), "pending": pending,
                       "done": sum(1 for p in poses if p["status"] == "done")}}


class PoseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    modifier: str = ""


@app.post("/api/projects/{project_id}/poses", status_code=201)
async def pose_add(project_id: int, body: PoseCreate) -> dict:
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(404, "project not found")
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 m FROM poses WHERE project_id = ?",
                           (project_id,)).fetchone()["m"]
        cur = conn.execute(
            "INSERT INTO poses (project_id, name, modifier, position) VALUES (?, ?, ?, ?)",
            (project_id, body.name.strip(), body.modifier.strip(), pos),
        )
        row = conn.execute("SELECT * FROM poses WHERE id = ?", (cur.lastrowid,)).fetchone()
    logs.info("process", f"pose added: {body.name}", project_id=project_id, pose_id=cur.lastrowid)
    return _pose_dict(row)


class PresetRequest(BaseModel):
    preset: str = "starter"


def _apply_preset(project_id: int, preset: str) -> int:
    """Add a preset's poses that don't already exist. Returns how many were added.
    Shared by the endpoint and the `lora_build` job handler. Raises on unknown preset /
    missing project."""
    items = PRESETS.get(preset)
    if not items:
        raise HTTPException(422, f"unknown preset '{preset}' (have: {list(PRESETS)})")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(404, "project not found")
        existing = {r["name"] for r in conn.execute(
            "SELECT name FROM poses WHERE project_id = ?", (project_id,))}
        base = conn.execute("SELECT COALESCE(MAX(position), 0) m FROM poses WHERE project_id = ?",
                           (project_id,)).fetchone()["m"]
        added = 0
        for i, (name, modifier) in enumerate(items, start=1):
            if name in existing:
                continue
            conn.execute("INSERT INTO poses (project_id, name, modifier, position) VALUES (?, ?, ?, ?)",
                         (project_id, name, modifier, base + i))
            added += 1
    logs.info("process", f"pose preset '{preset}' applied: +{added}", project_id=project_id)
    return added


@app.post("/api/projects/{project_id}/poses/preset")
async def pose_preset(project_id: int, body: PresetRequest) -> dict:
    return {"added": _apply_preset(project_id, body.preset), "preset": body.preset}


class PoseUpdate(BaseModel):
    name: str | None = None
    modifier: str | None = None


@app.patch("/api/projects/{project_id}/poses/{pose_id}")
async def pose_update(project_id: int, pose_id: int, body: PoseUpdate) -> dict:
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = ?"); vals.append(body.name.strip())
    if body.modifier is not None:
        sets.append("modifier = ?"); vals.append(body.modifier.strip())
    if not sets:
        raise HTTPException(422, "nothing to update")
    vals += [pose_id, project_id]
    with db.connect() as conn:
        cur = conn.execute(f"UPDATE poses SET {', '.join(sets)} WHERE id = ? AND project_id = ?", vals)
        if cur.rowcount == 0:
            raise HTTPException(404, "pose not found")
        row = conn.execute("SELECT * FROM poses WHERE id = ?", (pose_id,)).fetchone()
    return _pose_dict(row)


@app.delete("/api/projects/{project_id}/poses/{pose_id}")
async def pose_delete(project_id: int, pose_id: int) -> dict:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM poses WHERE id = ? AND project_id = ?", (pose_id, project_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "pose not found")
    return {"deleted": pose_id}


class PoseAiRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)


@app.post("/api/projects/{project_id}/poses/{pose_id}/ai")
async def pose_ai(project_id: int, pose_id: int, body: PoseAiRequest) -> dict:
    """Revise a pose's modifier via Ollama. Returns the suggestion; does not save."""
    with db.connect() as conn:
        row = conn.execute("SELECT modifier FROM poses WHERE id = ? AND project_id = ?",
                          (pose_id, project_id)).fetchone()
    if row is None:
        raise HTTPException(404, "pose not found")
    try:
        text = await ollama.revise(body.instruction, row["modifier"])
    except ollama.OllamaError as exc:
        raise HTTPException(502, f"AI assistant error: {exc}") from exc
    return {"modifier": text}


@app.post("/api/projects/{project_id}/poses/{pose_id}/generate")
async def pose_generate(project_id: int, pose_id: int) -> dict:
    detail = await get_project(project_id)
    version = detail["current_version"] or {}
    slug = detail["project"]["slug"]
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM poses WHERE id = ? AND project_id = ?",
                          (pose_id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, "pose not found")
        lora_cfg = _pose_lora_cfg(conn, project_id, slug)
        try:
            await _queue_pose(conn, project_id, slug, version, row, lora_cfg)
        except (workflows.WorkflowError, comfy.ComfyError) as exc:
            raise HTTPException(502, str(exc)) from exc
    return {"pose_id": pose_id, "status": "pending"}


async def _queue_all_poses(project_id: int) -> int:
    """Queue every pose for a project (with the project's LoRA if selected). Returns the
    count queued. Raises on a total failure (nothing queued). Shared by the endpoint and
    the `lora_build` job handler."""
    detail = await get_project(project_id)
    version = detail["current_version"] or {}
    slug = detail["project"]["slug"]
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM poses WHERE project_id = ? ORDER BY position, id",
                          (project_id,)).fetchall()
        if not rows:
            raise HTTPException(400, "no poses yet — add some or load a preset first")
        lora_cfg = _pose_lora_cfg(conn, project_id, slug)
        queued = 0
        for row in rows:
            try:
                await _queue_pose(conn, project_id, slug, version, row, lora_cfg)
                queued += 1
            except (workflows.WorkflowError, comfy.ComfyError) as exc:
                logs.error("process", f"pose queue failed after {queued}: {exc}", project_id=project_id)
                if queued == 0:
                    raise HTTPException(502, f"could not queue poses: {exc}") from exc
                break
    logs.info("process", f"poses: queued {queued} render(s)", project_id=project_id, slug=slug)
    return queued


@app.post("/api/projects/{project_id}/poses/generate-all")
async def poses_generate_all(project_id: int) -> dict:
    return {"queued": await _queue_all_poses(project_id)}


@app.get("/api/projects/{project_id}/pose-config")
async def pose_config(project_id: int) -> dict:
    """LoRA options for pose rendering: which trained LoRAs exist, which one is selected,
    and whether ComfyUI can actually see them (extra_model_paths + restart)."""
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    with db.connect() as conn:
        proj = conn.execute(
            "SELECT trigger_word, train_status, pose_lora, pose_lora_strength FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if proj is None:
        raise HTTPException(404, "project not found")

    lora_dir = BUILDS_ROOT / slug / "lora"
    files = sorted(p.name for p in lora_dir.glob("*.safetensors")) if lora_dir.is_dir() else []
    try:
        comfy_opts = set(await comfy.list_models("loras"))
    except Exception:  # noqa: BLE001
        comfy_opts = set()

    def visible(fn: str) -> bool:
        return f"{slug}/lora/{fn}" in comfy_opts

    return {
        "trigger_word": (proj["trigger_word"] or "") or default_trigger(slug),
        "train_status": proj["train_status"],
        "loras": [{"name": fn, "comfy_visible": visible(fn)} for fn in files],
        "selected": (proj["pose_lora"] or "").strip(),
        "strength": proj["pose_lora_strength"] or 1.0,
        # a trained LoRA exists on disk but ComfyUI can't see any of them → the user still
        # needs `loras: /builds` in extra_model_paths.yaml + a ComfyUI restart.
        "needs_extra_paths": bool(files) and not any(visible(fn) for fn in files),
    }


class PoseLoraRequest(BaseModel):
    lora: str = ""                                          # bare filename, or '' to disable
    strength: float = Field(default=1.0, ge=0.0, le=2.0)


@app.post("/api/projects/{project_id}/pose-lora")
async def set_pose_lora(project_id: int, body: PoseLoraRequest) -> dict:
    """Choose the trained LoRA to load into this project's pose renders ('' disables)."""
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    name = body.lora.strip()
    if name and not (BUILDS_ROOT / slug / "lora" / name).is_file():
        raise HTTPException(404, f"LoRA '{name}' not found in {BUILDS_ROOT / slug / 'lora'}")
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET pose_lora = ?, pose_lora_strength = ? WHERE id = ?",
            (name, body.strength, project_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "project not found")
    logs.info("process", f"pose LoRA {'set: ' + name if name else 'cleared'}",
              project_id=project_id, strength=body.strength)
    return {"pose_lora": name, "pose_lora_strength": body.strength}


# --------------------------------------------------------------------------- #
# export to SillyTavern (Phase D, 0.6.1): matte each rendered pose to a
# transparent PNG (BEN2) and save it under its SillyTavern filename. Staged into
# the build folder only — never auto-copied into ST (deliberate manual step).
# --------------------------------------------------------------------------- #

_EXPR_SET = set(EXPRESSIONS_28)
_SPRITE_RE = re.compile(r"[^a-z0-9]+")
_FOLDER_RE = re.compile(r"[^A-Za-z0-9 _-]+")


def _sprite_stem(pose_name: str) -> str:
    """The filename stem a pose exports under. An exact SillyTavern expression name
    (e.g. 'joy') is kept verbatim so ST recognises it; anything else is slugified."""
    low = pose_name.strip().lower()
    if low in _EXPR_SET:
        return low
    return _SPRITE_RE.sub("_", low).strip("_") or "pose"


def _export_char_folder(name: str) -> str:
    """A filesystem-safe character folder name derived from the project name."""
    cleaned = re.sub(r"\s+", " ", _FOLDER_RE.sub("", name)).strip()
    return cleaned or "character"


def _export_folder_path(slug: str, name: str) -> str:
    """Where the sprites land, relative to ComfyUI's output root (== /builds)."""
    return f"{slug}/export/{_export_char_folder(name)}"


async def _reconcile_export(project_id: int) -> None:
    """Flip queued export jobs to done/error as their BEN2 renders finish."""
    with db.connect() as conn:
        pending = conn.execute(
            "SELECT id, prompt_id FROM export_jobs WHERE project_id = ? AND status = 'pending' AND prompt_id != ''",
            (project_id,),
        ).fetchall()
    if not pending:
        return
    try:
        hist = await comfy.history_all()
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not read history to reconcile export: {exc}")
        return
    done = failed = 0
    with db.connect() as conn:
        for job in pending:
            entry = hist.get(job["prompt_id"])
            if not entry:
                continue
            st = comfy.status_of(entry)
            if st == "success":
                imgs = comfy.outputs_from(entry)
                if imgs:
                    conn.execute(
                        "UPDATE export_jobs SET filename = ?, subfolder = ?, status = 'done' WHERE id = ?",
                        (imgs[-1]["filename"], imgs[-1]["subfolder"], job["id"]),
                    )
                    done += 1
                    logs.verbose("process", "sprite exported", export_id=job["id"], file=imgs[-1]["filename"])
            elif st == "error":
                conn.execute("UPDATE export_jobs SET status = 'error' WHERE id = ?", (job["id"],))
                failed += 1
                logs.warn("process", "a sprite export failed", export_id=job["id"])
    if done or failed:
        logs.info("process", f"export reconcile: {done} done, {failed} failed", project_id=project_id)


@app.get("/api/projects/{project_id}/poses/export")
async def poses_export_status(project_id: int) -> dict:
    await _reconcile_export(project_id)
    detail = await get_project(project_id)
    name, slug = detail["project"]["name"], detail["project"]["slug"]
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(404, "project not found")
        rows = conn.execute(
            "SELECT * FROM export_jobs WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        exportable = conn.execute(
            "SELECT COUNT(*) n FROM poses WHERE project_id = ? AND status = 'done' AND filename != ''",
            (project_id,),
        ).fetchone()["n"]
    jobs = [dict(r) for r in rows]
    pending = sum(1 for j in jobs if j["status"] == "pending")
    sprites = [
        {"target_name": j["target_name"], "filename": j["filename"], "subfolder": j["subfolder"]}
        for j in jobs if j["status"] == "done" and j["filename"]
    ]
    return {
        "folder": _export_folder_path(slug, name),
        "exportable": exportable,
        "generating": pending > 0,
        "counts": {
            "total": len(jobs), "pending": pending,
            "done": sum(1 for j in jobs if j["status"] == "done"),
            "error": sum(1 for j in jobs if j["status"] == "error"),
        },
        "sprites": sprites,
    }


@app.post("/api/projects/{project_id}/poses/export")
async def poses_export(project_id: int) -> dict:
    """Matte every rendered pose to a transparent SillyTavern-named PNG via BEN2."""
    detail = await get_project(project_id)
    name, slug = detail["project"]["name"], detail["project"]["slug"]
    with db.connect() as conn:
        inflight = conn.execute(
            "SELECT COUNT(*) n FROM export_jobs WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        ).fetchone()["n"]
        if inflight:
            raise HTTPException(409, "an export is already in progress for this persona")
        poses = conn.execute(
            "SELECT * FROM poses WHERE project_id = ? AND status = 'done' AND filename != '' ORDER BY position, id",
            (project_id,),
        ).fetchall()
    if not poses:
        raise HTTPException(400, "no rendered poses to export — generate the set first")

    out_path = _export_folder_path(slug, name)
    up_folder = f"pf-export-{slug}"
    # a fresh export replaces the previous batch's job rows
    with db.connect() as conn:
        conn.execute("DELETE FROM export_jobs WHERE project_id = ?", (project_id,))

    logs.info("process", f"exporting {len(poses)} pose(s) → transparent sprites in {out_path}",
              project_id=project_id, folder=out_path)
    queued, errors, used = 0, [], {}
    for p in poses:
        stem = _sprite_stem(p["name"])
        seen = used.get(stem, 0)
        used[stem] = seen + 1
        if seen:  # two poses map to the same name — don't let them overwrite each other
            stem = f"{stem}_{seen + 1}"
        src = BUILDS_ROOT / (p["subfolder"] or f"{slug}/images") / p["filename"]
        try:
            logs.verbose("local", "reading rendered pose off builds share", path=str(src))
            data = src.read_bytes()
            up = await comfy.upload_image(data, f"pose_{p['id']}.png", up_folder)
            img_ref = f"{up['subfolder']}/{up['name']}" if up.get("subfolder") else up["name"]
            graph = workflows.build_graph("bg-remove", {
                "image": img_ref,
                "output_path": out_path,
                "filename_prefix": stem,
                "add_background": "none",
            })
            prompt_id = await comfy.submit(graph)
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO export_jobs (project_id, pose_id, prompt_id, target_name) VALUES (?, ?, ?, ?)",
                    (project_id, p["id"], prompt_id, f"{stem}.png"),
                )
            queued += 1
        except FileNotFoundError:
            logs.warn("local", "pose image missing on the builds share", path=str(src))
            errors.append(f"{p['name']}: source image not found on /builds")
        except (workflows.WorkflowError, comfy.ComfyError) as exc:
            logs.error("integration", f"export queue failed for pose {p['id']}: {exc}", path=str(src))
            errors.append(f"{p['name']}: {exc}")
    if queued == 0:
        raise HTTPException(502, f"could not queue any exports: {errors[:3]}")
    logs.info("process", f"export queued {queued}/{len(poses)} sprite(s)",
              project_id=project_id, folder=out_path, failed=len(errors))
    return {"queued": queued, "folder": out_path, "errors": errors[:5]}


# --------------------------------------------------------------------------- #
# Job engine (Phase 7, 0.7.0): the `lora_build` handler + generic job endpoints.
# The engine itself lives in jobs.py; handlers live here because they orchestrate
# the same train/pose helpers the endpoints use. See jobs.py for the worker.
# --------------------------------------------------------------------------- #

# How long to wait for ComfyUI to come back + rescan its LoRA folder after a
# post-training restart before giving up and rendering base-character poses.
BUILD_BIND_GRACE_SECONDS = float(os.getenv("BUILD_BIND_GRACE_SECONDS", "300"))


async def _project_slug(project_id: int) -> str | None:
    with db.connect() as conn:
        r = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()
    return r["slug"] if r else None


def _newest_lora_file(slug: str) -> str | None:
    d = BUILDS_ROOT / slug / "lora"
    files = sorted(d.glob("*.safetensors"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    return files[-1].name if files else None


async def _lora_comfy_visible(slug: str, filename: str) -> bool:
    try:
        return f"{slug}/lora/{filename}" in await comfy.list_models("loras")
    except Exception:  # noqa: BLE001
        return False


class LoraBuildHandler:
    """Unattended build: train LoRA → bind it (restart ComfyUI if it can't see the new
    file) → render the expression set. Every step reconciles from ComfyUI history so it
    resumes after a container restart. Degrades to base-character poses (with a clear
    note) if ComfyUI can't be made to load the LoRA."""

    async def tick(self, job: dict[str, Any]) -> tuple[str, str]:
        pid = job["project_id"]
        params = jobs.params_of(job)
        state = jobs.state_of(job)
        stage = job["stage"]
        slug = await _project_slug(pid)
        if slug is None:
            return jobs.ERROR, "project no longer exists"

        # stage 0 — kick off training
        if stage == "":
            steps = int(params.get("steps", 500))
            rank = int(params.get("rank", 16))
            lr = float(params.get("learning_rate", 0.0005))
            try:
                res = await _start_lora_training(pid, steps, rank, lr)
            except TrainNotReady as exc:
                return jobs.ERROR, str(exc)
            except (workflows.WorkflowError, comfy.ComfyError) as exc:
                return jobs.ERROR, f"could not start training: {exc}"
            state.update(train_prompt_id=res["prompt_id"], steps=steps)
            jobs.set_state(job["id"], state)
            jobs.set_stage(job["id"], "training", f"training LoRA — {steps} steps", 0.05)
            return jobs.RUNNING, ""

        # stage 1 — wait for training to finish
        if stage == "training":
            await _reconcile_training(pid)
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT train_status, train_started_at, last_train_seconds "
                    "FROM projects WHERE id = ?", (pid,)).fetchone()
            ts = row["train_status"] if row else "error"
            if ts == "training":
                elapsed = time.time() - (row["train_started_at"] or time.time())
                jobs.set_message(job["id"], f"training LoRA — {_fmt_dur(elapsed)} elapsed", 0.2)
                return jobs.RUNNING, ""
            if ts == "error":
                return jobs.ERROR, "LoRA training failed (see logs)"
            lora = _newest_lora_file(slug)
            if not lora:
                return jobs.ERROR, "training finished but no LoRA file was written"
            strength = float(params.get("lora_strength", 1.0))
            with db.connect() as conn:
                conn.execute("UPDATE projects SET pose_lora = ?, pose_lora_strength = ? WHERE id = ?",
                             (lora, strength, pid))
            try:
                _apply_preset(pid, params.get("preset", "expressions"))
            except HTTPException as exc:
                return jobs.ERROR, f"could not load expression preset: {exc.detail}"
            state.update(lora=lora, train_seconds=round(row["last_train_seconds"] or 0, 1))
            jobs.set_state(job["id"], state)
            jobs.set_stage(job["id"], "binding", "making the trained LoRA visible to ComfyUI", 0.5)
            return jobs.RUNNING, ""

        # stage 2 — bind: ensure ComfyUI can load the new LoRA (restart it if needed)
        if stage == "binding":
            lora = state.get("lora", "")
            if await _lora_comfy_visible(slug, lora):
                return await self._start_render(job, pid, state, degraded=False)
            if not state.get("restart_requested"):
                if docker_ctl.enabled():
                    try:
                        await docker_ctl.restart("comfyui")
                    except docker_ctl.DockerCtlError as exc:
                        logs.warn("integration", f"could not restart ComfyUI to bind LoRA: {exc}")
                        return await self._start_render(job, pid, state, degraded=True)
                    state.update(restart_requested=True, restart_at=time.time())
                    jobs.set_state(job["id"], state)
                    jobs.set_message(job["id"], "restarting ComfyUI to load the new LoRA…", 0.55)
                    return jobs.RUNNING, ""
                logs.warn("process", "ComfyUI can't see the new LoRA and container control is off "
                          "— rendering base-character poses", project_id=pid)
                return await self._start_render(job, pid, state, degraded=True)
            # already asked for a restart — wait for ComfyUI to come back, then rescan
            try:
                await comfy.system_stats()
                back = True
            except Exception:  # noqa: BLE001
                back = False
            if not back:
                jobs.set_message(job["id"], "waiting for ComfyUI to restart…", 0.58)
                return jobs.RUNNING, ""
            if await _lora_comfy_visible(slug, lora):
                return await self._start_render(job, pid, state, degraded=False)
            if time.time() - state.get("restart_at", 0) > BUILD_BIND_GRACE_SECONDS:
                logs.warn("process", "ComfyUI restarted but still can't see the LoRA "
                          "— rendering base-character poses", project_id=pid)
                return await self._start_render(job, pid, state, degraded=True)
            jobs.set_message(job["id"], "ComfyUI back — waiting for the LoRA to appear…", 0.6)
            return jobs.RUNNING, ""

        # stage 3 — render the expression set
        if stage == "rendering":
            await _reconcile_poses(pid)
            with db.connect() as conn:
                rows = conn.execute("SELECT status FROM poses WHERE project_id = ?", (pid,)).fetchall()
            total = len(rows)
            done = sum(1 for r in rows if r["status"] == "done")
            pending = sum(1 for r in rows if r["status"] == "pending")
            errored = sum(1 for r in rows if r["status"] == "error")
            if pending == 0:
                degraded = bool(state.get("degraded"))
                jobs.set_result(job["id"], {
                    "lora": state.get("lora"), "lora_bound": not degraded,
                    "train_seconds": state.get("train_seconds"),
                    "poses_total": total, "poses_done": done, "poses_error": errored,
                })
                note = ("" if not degraded else
                        " (ComfyUI couldn't load the LoRA — rendered base poses; restart ComfyUI "
                        "and regenerate to bind it)")
                return jobs.DONE, f"built LoRA + {done}/{total} expressions{note}"
            frac = 0.6 + 0.4 * (done / total if total else 0)
            jobs.set_message(job["id"], f"rendering expressions — {done}/{total}", frac)
            return jobs.RUNNING, ""

        return jobs.ERROR, f"unknown build stage '{stage}'"

    async def _start_render(self, job: dict[str, Any], pid: int, state: dict[str, Any],
                            degraded: bool) -> tuple[str, str]:
        if degraded:
            with db.connect() as conn:  # drop the LoRA so poses render from the base character
                conn.execute("UPDATE projects SET pose_lora = '' WHERE id = ?", (pid,))
            state["degraded"] = True
        jobs.set_state(job["id"], state)
        try:
            queued = await _queue_all_poses(pid)
        except HTTPException as exc:
            return jobs.ERROR, f"could not queue expressions: {exc.detail}"
        jobs.set_stage(job["id"], "rendering",
                       f"rendering {queued} expressions" + (" (base — LoRA unbound)" if degraded else ""),
                       0.6)
        return jobs.RUNNING, ""


jobs.register("lora_build", LoraBuildHandler())


def _job_dict(job: dict[str, Any]) -> dict[str, Any]:
    d = dict(job)
    d["params"] = jobs.params_of(job)
    d["state"] = jobs.state_of(job)
    try:
        d["result"] = json.loads(job.get("result_json") or "{}")
    except json.JSONDecodeError:
        d["result"] = {}
    for k in ("params_json", "state_json", "result_json"):
        d.pop(k, None)
    return d


class JobCreate(BaseModel):
    kind: str = "lora_build"
    params: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/projects/{project_id}/jobs", status_code=201)
async def create_job(project_id: int, body: JobCreate) -> dict:
    """Enqueue a background job for a project (kind='lora_build' = train → 28 expressions)."""
    if body.kind not in jobs.HANDLERS:
        raise HTTPException(422, f"unknown job kind '{body.kind}' (have: {sorted(jobs.HANDLERS)})")
    detail = await get_project(project_id)  # 404 if missing
    for j in jobs.list_jobs(project_id):
        if j["kind"] == body.kind and j["status"] in ("queued", "running"):
            raise HTTPException(409, f"a {body.kind} job is already {j['status']} for this project")
    if body.kind == "lora_build":
        slug = detail["project"]["slug"]
        if not await _dataset_folder_exists(_input_folder(slug)):
            raise HTTPException(400, "dataset not staged — stage the dataset before starting a build")
    return _job_dict(jobs.enqueue(body.kind, project_id, body.params))


@app.get("/api/projects/{project_id}/jobs")
async def list_project_jobs(project_id: int) -> dict:
    return {"jobs": [_job_dict(j) for j in jobs.list_jobs(project_id)]}


@app.get("/api/jobs")
async def list_all_jobs() -> dict:
    return {"jobs": [_job_dict(j) for j in jobs.list_jobs(None)]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int) -> dict:
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return _job_dict(j)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int) -> dict:
    j = jobs.cancel(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    # A running lora_build has ComfyUI work in flight (training, then pose renders). The
    # cooperative cancel only stops the pipeline *advancing* — the GPU keeps churning on the
    # already-submitted prompt until it finishes. So also interrupt ComfyUI and clear its
    # pending queue to free the GPU immediately. Best-effort: never fail the cancel if ComfyUI
    # is unreachable (the job is flagged regardless and the worker will finalize it).
    if j["status"] == jobs.RUNNING and j["kind"] == "lora_build":
        try:
            await comfy.interrupt()
            await comfy.clear_pending()
            logs.info("process", "stop: interrupted ComfyUI to free the GPU", job_id=job_id)
        except Exception as exc:  # noqa: BLE001
            logs.warn("integration", f"stop: could not interrupt ComfyUI: {exc}", job_id=job_id)
        # Reset the project's train flag so a stopped build can't leave it stuck at 'training'
        # (which would block every future build with 'a training run is already in progress').
        if j["project_id"] is not None:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE projects SET train_status = 'error' "
                    "WHERE id = ? AND train_status = 'training'", (j["project_id"],))
    return _job_dict(j)


# --- static frontend -------------------------------------------------------
class NoCacheStaticFiles(StaticFiles):
    """Serve the frontend with `Cache-Control: no-cache` so a browser always
    revalidates. StaticFiles still sends ETag/Last-Modified, so an unchanged
    asset returns a cheap 304 — but a NEW build after a deploy is picked up on
    the next load instead of the browser silently serving stale JS (which showed
    up as an old UI against a new backend)."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


if FRONTEND_DIR.is_dir():
    app.mount("/static", NoCacheStaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})
