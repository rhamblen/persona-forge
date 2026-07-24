"""Persona Forge API.

0.1.x proved the deploy loop + infrastructure checks.
0.2.x adds the Prompt Studio foundations: named projects (each backed by a build
folder), an append-only prompt version history with sign-off + rollback, and
generation through ComfyUI via workflow templates + manifests.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import comfy, db, workflows

COMFYUI_URL = comfy.COMFYUI_URL
BUILDS_ROOT = Path(os.getenv("BUILDS_ROOT", "/builds"))
APPDATA_ROOT = Path(os.getenv("APPDATA_ROOT", "/appdata"))
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


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


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
        "appdata_root": str(APPDATA_ROOT),
        "appdata_mounted": APPDATA_ROOT.is_dir(),
    }


# --------------------------------------------------------------------------- #
# models + workflows
# --------------------------------------------------------------------------- #

@app.get("/api/models")
async def models(kind: str = "checkpoints") -> dict:
    try:
        return {"kind": kind, "models": await comfy.list_models(kind)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read models from ComfyUI: {exc}") from exc


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
# projects  (a project == a named build folder)
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(400, "project name must contain at least one alphanumeric character")
    return slug


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

    if not BUILDS_ROOT.is_dir():
        raise HTTPException(503, f"builds root not mounted at {BUILDS_ROOT}")
    if build_dir.exists():
        raise HTTPException(409, f"a build folder named '{slug}' already exists")

    try:
        (build_dir / "lora").mkdir(parents=True)
        (build_dir / "images").mkdir(parents=True)
    except OSError as exc:
        raise HTTPException(500, f"could not create build folder: {exc}") from exc

    with db.connect() as conn:
        cur = conn.execute("INSERT INTO projects (name, slug) VALUES (?, ?)", (body.name, slug))
        project_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO prompt_versions
               (project_id, parent_id, character, style, negative, checkpoint, seed, source, note)
               VALUES (?, NULL, ?, ?, ?, ?, ?, 'initial', 'initial version')""",
            (project_id, body.character, body.style, body.negative, body.checkpoint, body.seed),
        )
        conn.execute(
            "UPDATE projects SET current_version_id = ? WHERE id = ?", (cur.lastrowid, project_id)
        )

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
    return await get_project(project_id)


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
        raise HTTPException(502, f"ComfyUI execution error — {err}")

    images = comfy.outputs_from(entry)
    with db.connect() as conn:
        for img in images:
            conn.execute(
                """INSERT INTO images (project_id, version_id, filename, subfolder, kind)
                   VALUES (?, ?, ?, ?, 'preview')""",
                (project_id, version.get("id"), img["filename"], img["subfolder"]),
            )

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


# --- static frontend -------------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")
