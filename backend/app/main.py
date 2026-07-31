"""Persona Forge API.

0.1.x proved the deploy loop + infrastructure checks.
0.2.x adds the Prompt Studio foundations: named projects (each backed by a build
folder), an append-only prompt version history with sign-off + rollback, and
generation through ComfyUI via workflow templates + manifests.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import random
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import comfy, db, docker_ctl, jobs, logs, ollama, skeleton, workflows

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

def _builds_path(subfolder: str, filename: str) -> Path | None:
    """Resolve subfolder/filename inside the builds root. Returns None for an empty
    filename or a crafted path that escapes /builds."""
    if not filename:
        return None
    path = (BUILDS_ROOT / (subfolder or "") / filename).resolve()
    try:
        path.relative_to(BUILDS_ROOT.resolve())
    except ValueError:
        logs.warn("local", "refusing a path outside the builds root", path=str(path))
        return None
    return path


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
        seed_emotion_map()   # first boot only; an edited map is never overwritten
        seed_axis_families()  # needs the map above to resolve each axis's top tier
        backfill_pose_axes()
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


# --------------------------------------------------------------------------- #
# concept-LoRA library (Phase H1b) — pose/gesture/expression LoRAs that carry *what
# the body is doing*, reused across every character, as opposed to the per-character
# LoRA that carries *who*. Global on purpose: reuse is the whole point.
# --------------------------------------------------------------------------- #

CONCEPT_CATEGORIES = ("pose", "gesture", "expression", "style")


class ConceptLoraIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1)
    base_model: str = ""
    category: str = "pose"
    trigger_words: str = ""
    weight_min: float = Field(default=0.4, ge=0.0, le=2.0)
    weight_max: float = Field(default=0.8, ge=0.0, le=2.0)
    notes: str = ""


class ConceptLoraPatch(BaseModel):
    name: str | None = None
    base_model: str | None = None
    category: str | None = None
    trigger_words: str | None = None
    weight_min: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_max: float | None = Field(default=None, ge=0.0, le=2.0)
    notes: str | None = None


@app.get("/api/concept-loras")
async def concept_loras_list() -> dict:
    """The library, annotated with whether ComfyUI can actually still see each file.

    A missing file is reported rather than hidden — a stack referencing it would fail at
    submit time, and a stale library entry is exactly the kind of thing worth surfacing.
    """
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM concept_loras ORDER BY category, name").fetchall()
    try:
        available = set(await comfy.list_models("loras"))
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not list loras to check the concept library: {exc}")
        available = None
    return {
        "concept_loras": [
            {**dict(r), "available": (None if available is None else r["filename"] in available)}
            for r in rows
        ],
        "categories": list(CONCEPT_CATEGORIES),
    }


@app.post("/api/concept-loras", status_code=201)
async def concept_lora_add(body: ConceptLoraIn) -> dict:
    if body.category not in CONCEPT_CATEGORIES:
        raise HTTPException(400, f"category must be one of {', '.join(CONCEPT_CATEGORIES)}")
    if body.weight_max < body.weight_min:
        raise HTTPException(400, "weight_max must be >= weight_min")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM concept_loras WHERE filename = ?",
                        (body.filename,)).fetchone():
            raise HTTPException(409, "that LoRA file is already in the library")
        cur = conn.execute(
            """INSERT INTO concept_loras
               (name, filename, base_model, category, trigger_words, weight_min, weight_max, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.name, body.filename, body.base_model, body.category,
             body.trigger_words, body.weight_min, body.weight_max, body.notes),
        )
        row = conn.execute("SELECT * FROM concept_loras WHERE id = ?", (cur.lastrowid,)).fetchone()
    # NB: `category=` would collide with logs.info's own first parameter
    logs.info("process", f"concept LoRA '{body.name}' added to the library",
              filename=body.filename, kind=body.category)
    return {"concept_lora": dict(row)}


@app.patch("/api/concept-loras/{lora_id}")
async def concept_lora_update(lora_id: int, body: ConceptLoraPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "category" in fields and fields["category"] not in CONCEPT_CATEGORIES:
        raise HTTPException(400, f"category must be one of {', '.join(CONCEPT_CATEGORIES)}")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM concept_loras WHERE id = ?", (lora_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "concept LoRA not found")
        lo = fields.get("weight_min", row["weight_min"])
        hi = fields.get("weight_max", row["weight_max"])
        if hi < lo:
            raise HTTPException(400, "weight_max must be >= weight_min")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE concept_loras SET {sets} WHERE id = ?", (*fields.values(), lora_id))
        row = conn.execute("SELECT * FROM concept_loras WHERE id = ?", (lora_id,)).fetchone()
    return {"concept_lora": dict(row)}


@app.delete("/api/concept-loras/{lora_id}")
async def concept_lora_delete(lora_id: int) -> dict:
    """Remove a library entry. Stacks already built keep working — they store the
    filename, not a foreign key, precisely so curating the library can't break a version."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM concept_loras WHERE id = ?", (lora_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "concept LoRA not found")
        conn.execute("DELETE FROM concept_loras WHERE id = ?", (lora_id,))
    logs.info("process", f"concept LoRA '{row['name']}' removed from the library")
    return {"deleted": lora_id}


# --------------------------------------------------------------------------- #
# ControlNet registry (Phase H3, 0.8.4)
#
# Same shape as the concept-LoRA library above, for the same reason: a ControlNet only
# works on the checkpoint family it was trained for, and this project deliberately isn't
# committed to one. Registering both a NoobAI-native and a generic SDXL openpose model
# means moving a persona's checkpoint doesn't strand pose control.
# --------------------------------------------------------------------------- #

CONTROLNET_KINDS = ("openpose", "depth", "canny", "union")

# Seeded on first read for whichever files ComfyUI actually reports — lazily rather than
# at boot so a ComfyUI that is down doesn't block startup, and so nothing is invented for
# a file that isn't installed.
DEFAULT_CONTROLNETS = [
    {
        "name": "NoobAI openpose (native)",
        "filename": "noobai-openpose-sdxl.safetensors",
        "base_model": "noobai-xl / illustrious",
        "kind": "openpose",
        "notes": "Laxhar/noob_openpose, trained on NoobAI-XL. First choice for "
                 "NoobAI/Illustrious checkpoints.",
    },
    {
        "name": "xinsir openpose SDXL 1.0",
        "filename": "xinsir-openpose-sdxl-1.0.safetensors",
        "base_model": "sdxl",
        "kind": "openpose",
        "notes": "Generic SDXL openpose — the fallback when the checkpoint is not "
                 "NoobAI/Illustrious.",
    },
]


class ControlNetIn(BaseModel):
    name: str
    filename: str
    base_model: str = ""
    kind: str = "openpose"
    notes: str = ""


class ControlNetPatch(BaseModel):
    name: str | None = None
    base_model: str | None = None
    kind: str | None = None
    notes: str | None = None


def _seed_controlnets(conn, available: set[str] | None) -> int:
    """Register the shipped defaults that are actually on disk. Idempotent."""
    if available is None:
        return 0
    have = {r["filename"] for r in conn.execute("SELECT filename FROM controlnets")}
    added = 0
    for d in DEFAULT_CONTROLNETS:
        if d["filename"] in have or d["filename"] not in available:
            continue
        conn.execute(
            "INSERT INTO controlnets (name, filename, base_model, kind, notes) VALUES (?, ?, ?, ?, ?)",
            (d["name"], d["filename"], d["base_model"], d["kind"], d["notes"]),
        )
        added += 1
    if added:
        logs.info("boot", f"registered {added} installed ControlNet(s) from the shipped defaults")
    return added


@app.get("/api/controlnets")
async def controlnets_list() -> dict:
    """The registry, annotated with whether ComfyUI can still see each file."""
    try:
        available: set[str] | None = set(await comfy.list_models("controlnet"))
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not list controlnet models: {exc}")
        available = None
    with db.connect() as conn:
        _seed_controlnets(conn, available)
        rows = conn.execute("SELECT * FROM controlnets ORDER BY kind, name").fetchall()
    return {
        "controlnets": [
            {**dict(r), "available": (None if available is None else r["filename"] in available)}
            for r in rows
        ],
        "installed": sorted(available) if available is not None else [],
        "kinds": list(CONTROLNET_KINDS),
    }


@app.post("/api/controlnets", status_code=201)
async def controlnet_add(body: ControlNetIn) -> dict:
    if body.kind not in CONTROLNET_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(CONTROLNET_KINDS)}")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM controlnets WHERE filename = ?", (body.filename,)).fetchone():
            raise HTTPException(409, "that ControlNet file is already registered")
        cur = conn.execute(
            "INSERT INTO controlnets (name, filename, base_model, kind, notes) VALUES (?, ?, ?, ?, ?)",
            (body.name, body.filename, body.base_model, body.kind, body.notes),
        )
        row = conn.execute("SELECT * FROM controlnets WHERE id = ?", (cur.lastrowid,)).fetchone()
    logs.info("process", f"ControlNet '{body.name}' registered", filename=body.filename)
    return {"controlnet": dict(row)}


@app.patch("/api/controlnets/{cn_id}")
async def controlnet_update(cn_id: int, body: ControlNetPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "kind" in fields and fields["kind"] not in CONTROLNET_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(CONTROLNET_KINDS)}")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM controlnets WHERE id = ?", (cn_id,)).fetchone() is None:
            raise HTTPException(404, "ControlNet not found")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE controlnets SET {sets} WHERE id = ?", (*fields.values(), cn_id))
        row = conn.execute("SELECT * FROM controlnets WHERE id = ?", (cn_id,)).fetchone()
    return {"controlnet": dict(row)}


@app.delete("/api/controlnets/{cn_id}")
async def controlnet_delete(cn_id: int) -> dict:
    """Unregister. Personas selecting it fall back to prompt-only pose renders rather
    than failing — the same posture the LoRA delete takes."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM controlnets WHERE id = ?", (cn_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "ControlNet not found")
        conn.execute("DELETE FROM controlnets WHERE id = ?", (cn_id,))
        cleared = conn.execute(
            "UPDATE projects SET pose_controlnet = '' WHERE pose_controlnet = ?",
            (row["filename"],),
        ).rowcount
    logs.info("process", f"ControlNet '{row['name']}' unregistered", cleared_from=cleared)
    return {"deleted": cn_id, "cleared_from_personas": cleared}


# --------------------------------------------------------------------------- #
# Pose library (Phase H3b, 0.8.5)
#
# Global, like the concept-LoRA and ControlNet libraries: a skeleton is character-agnostic,
# which is exactly why curating one set is worth the effort.
#
# Note on sourcing (measured, docs/pose-control.md §4): DWPose extraction returns "no person
# detected" for kneeling/sitting/lying anime figures, so **hand-authored keypoints are the
# primary source** for grounded poses rather than something to be replaced by an importer.
# --------------------------------------------------------------------------- #

POSE_CATEGORIES = ("standing", "grounded", "props", "monster")
POSE_FRAMINGS = ("full", "cowboy", "bust")


class PoseLibraryIn(BaseModel):
    name: str
    keypoints: list[Any]
    category: str = "standing"
    framing: str = "full"
    prompt_hint: str = ""
    prop_slot: str = ""
    face_visible: bool = True
    notes: str = ""
    parent_id: int | None = None


class PoseLibraryPatch(BaseModel):
    name: str | None = None
    keypoints: list[Any] | None = None
    category: str | None = None
    framing: str | None = None
    prompt_hint: str | None = None
    prop_slot: str | None = None
    face_visible: bool | None = None
    notes: str | None = None


def seed_pose_library(force: bool = False) -> int:
    """Write the shipped starter set into the DB. No-op once anything is in there.

    Same posture as the emotion map: the shipped set is a **starting point, not the
    vocabulary** — entries are editable and deletable, and a user who empties the library
    deliberately does not get it silently refilled on the next boot.

    `force` (the Restore button) TOPS UP by name rather than replacing the built-ins. The
    shipped catalogue grows between releases — 0.8.9 added three arms-wide variants — and
    the destructive version made collecting them cost every edit made to a built-in entry.
    Anything already present, edited or not, is left alone; to restore one to shipped state,
    delete it and restore again.
    """
    with db.connect() as conn:
        have = {r["name"] for r in conn.execute("SELECT name FROM pose_library")}
        if not force and have:
            return 0
        added = 0
        for p in (q for q in skeleton.STARTER_POSES if q["name"] not in have):
            conn.execute(
                """INSERT INTO pose_library
                   (name, category, framing, keypoints_json, prompt_hint, face_visible,
                    family, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'builtin')""",
                (p["name"], p["category"], p["framing"], json.dumps(p["points"]),
                 p.get("prompt_hint", ""), 1 if p.get("face_visible", True) else 0,
                 skeleton.family_for_name(p["name"], p["category"])))
            added += 1
    if added:
        logs.info("boot", f"seeded the pose library with {added} starter pose(s)")
    return added


POSE_FAMILIES = skeleton.FAMILIES

# Which family an axis poses in by default. Everything stands; the axes whose top rung is
# a collapse get grounded at that rung only — sorrow stands where despair sits. These are a
# STARTING POINT: /api/pose-families rewrites any of it, including per-tier.
_DEFAULT_AXIS_FAMILY = "standing"
_DEFAULT_TOP_TIER_FAMILY = {
    "sadness": "sitting",     # grief/despair goes to the floor
    "shame": "kneeling",      # humiliation folds down
    "fear": "kneeling",       # terror cowers
    "affection": "crouching",  # top-tier affection lowers itself to meet someone
}


def seed_axis_families(force: bool = False) -> int:
    """Seed the axis -> family map. No-op once anything is in there.

    Tier ramps are resolved against the LIVE emotion map rather than hard-coded tier
    numbers, because an axis's ladder length is editable — "the top rung" is the only
    stable way to say "when this emotion bottoms out, put them on the floor".
    """
    with db.connect() as conn:
        if force:
            conn.execute("DELETE FROM axis_pose_families")
        elif conn.execute("SELECT 1 FROM axis_pose_families LIMIT 1").fetchone():
            return 0
    n = 0
    with db.connect() as conn:
        for group in emotion_map():
            axis = group["axis"]
            conn.execute(
                "INSERT OR REPLACE INTO axis_pose_families (axis, tier, family) VALUES (?, NULL, ?)",
                (axis, _DEFAULT_AXIS_FAMILY))
            n += 1
            fam = _DEFAULT_TOP_TIER_FAMILY.get(axis)
            tiers = group["tiers"]
            if fam and tiers:
                top = max(t["position"] for t in tiers)
                conn.execute(
                    "INSERT OR REPLACE INTO axis_pose_families (axis, tier, family) VALUES (?, ?, ?)",
                    (axis, top, fam))
                n += 1
    if n:
        logs.info("boot", f"seeded {n} axis/tier pose-family assignment(s)")
    return n


def axis_family_map(project_id: int | None = None) -> dict[str, dict[str, Any]]:
    """axis -> {'default': family, 'tiers': {...}, 'entries': {...}} for one persona.

    Global rows (project_id IS NULL) are read first and a persona's own rows overwrite them
    key by key, so a character inherits the shipped mapping and diverges only where it has
    been told to. Ordering the query NULLs-first is what makes that overlay work.
    """
    out: dict[str, dict[str, Any]] = {}
    with db.connect() as conn:
        for r in conn.execute(
                "SELECT axis, tier, family, entry_id FROM axis_pose_families "
                "WHERE project_id IS NULL OR project_id = ? "
                "ORDER BY (project_id IS NOT NULL), id", (project_id,)):
            e = out.setdefault(r["axis"], {"default": None, "tiers": {}, "entries": {}})
            if r["tier"] is None:
                e["default"] = r["family"]
                e["entries"][None] = r["entry_id"]
            else:
                e["tiers"][r["tier"]] = r["family"]
                e["entries"][r["tier"]] = r["entry_id"]
    return out


def _family_for(axis: str | None, tier: int | None,
                project_id: int | None = None) -> tuple[str | None, int | None]:
    """(family, pinned entry id) for this pose — a tier row wins over the axis row."""
    if not axis:
        return None, None
    e = axis_family_map(project_id).get(axis)
    if not e:
        return None, None
    if tier is not None and tier in e["tiers"]:
        return e["tiers"][tier], e["entries"].get(tier)
    return e["default"], e["entries"].get(None)


def _family_members(conn, family: str) -> list[Any]:
    return conn.execute(
        "SELECT id, name, keypoints_json, face_visible, prompt_hint FROM pose_library "
        "WHERE family = ? ORDER BY id", (family,)).fetchall()


def _family_pick(conn, family: str, pose_name: str) -> Any | None:
    """Choose one member of `family` for this pose, stably and spread across the set.

    Keyed on the pose NAME, not its row id: a persona rebuilt from scratch keeps the same
    figure per emotion, and two poses in the same family land on different members instead
    of the whole axis rendering one identical stance.
    """
    members = _family_members(conn, family)
    if not members:
        return None
    h = int(hashlib.sha256(pose_name.strip().lower().encode()).hexdigest()[:8], 16)
    return members[h % len(members)]


def _pose_lib_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        d["keypoints"] = json.loads(d.pop("keypoints_json") or "[]")
    except json.JSONDecodeError:
        d["keypoints"] = []
    d["face_visible"] = bool(d["face_visible"])
    return d


@app.get("/api/pose-library")
async def pose_library_list() -> dict:
    seed_pose_library()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM pose_library ORDER BY category, name").fetchall()
    return {
        "poses": [_pose_lib_dict(r) for r in rows],
        "categories": list(POSE_CATEGORIES),
        "framings": list(POSE_FRAMINGS),
    }


@app.get("/api/pose-library/{pose_id}/preview.png")
async def pose_library_preview(pose_id: int, width: int = 208, height: int = 304) -> Any:
    """Render an entry's skeleton at any size — the picker's thumbnail, and the proof that
    what is stored is keypoints rather than a picture."""
    if not (32 <= width <= 2048 and 32 <= height <= 2048):
        raise HTTPException(400, "preview size out of range")
    with db.connect() as conn:
        row = conn.execute("SELECT keypoints_json FROM pose_library WHERE id = ?",
                           (pose_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "pose not found")
    try:
        png = skeleton.render_png(json.loads(row["keypoints_json"]), width, height)
    except (json.JSONDecodeError, skeleton.SkeletonError) as exc:
        raise HTTPException(422, f"stored keypoints are not renderable: {exc}") from exc
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-cache"})


@app.post("/api/pose-library", status_code=201)
async def pose_library_add(body: PoseLibraryIn) -> dict:
    if body.category not in POSE_CATEGORIES:
        raise HTTPException(400, f"category must be one of {', '.join(POSE_CATEGORIES)}")
    if body.framing not in POSE_FRAMINGS:
        raise HTTPException(400, f"framing must be one of {', '.join(POSE_FRAMINGS)}")
    try:
        skeleton.normalise_points(body.keypoints)      # validate before storing
    except skeleton.SkeletonError as exc:
        raise HTTPException(400, str(exc)) from exc
    source = "edited" if body.parent_id else "imported"
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO pose_library (name, category, framing, keypoints_json, prompt_hint,
                                         prop_slot, face_visible, source, parent_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.name, body.category, body.framing, json.dumps(body.keypoints),
             body.prompt_hint, body.prop_slot, 1 if body.face_visible else 0,
             source, body.parent_id, body.notes))
        row = conn.execute("SELECT * FROM pose_library WHERE id = ?", (cur.lastrowid,)).fetchone()
    logs.info("process", f"pose '{body.name}' added to the library", kind=body.category)
    return {"pose": _pose_lib_dict(row)}


@app.patch("/api/pose-library/{pose_id}")
async def pose_library_update(pose_id: int, body: PoseLibraryPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "category" in fields and fields["category"] not in POSE_CATEGORIES:
        raise HTTPException(400, f"category must be one of {', '.join(POSE_CATEGORIES)}")
    if "framing" in fields and fields["framing"] not in POSE_FRAMINGS:
        raise HTTPException(400, f"framing must be one of {', '.join(POSE_FRAMINGS)}")
    if "keypoints" in fields:
        try:
            skeleton.normalise_points(fields["keypoints"])
        except skeleton.SkeletonError as exc:
            raise HTTPException(400, str(exc)) from exc
        fields["keypoints_json"] = json.dumps(fields.pop("keypoints"))
    if "face_visible" in fields:
        fields["face_visible"] = 1 if fields["face_visible"] else 0
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM pose_library WHERE id = ?", (pose_id,)).fetchone() is None:
            raise HTTPException(404, "pose not found")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE pose_library SET {sets} WHERE id = ?", (*fields.values(), pose_id))
        row = conn.execute("SELECT * FROM pose_library WHERE id = ?", (pose_id,)).fetchone()
    return {"pose": _pose_lib_dict(row)}


@app.delete("/api/pose-library/{pose_id}")
async def pose_library_delete(pose_id: int) -> dict:
    """Remove an entry. Poses already rendered from it keep their staged skeleton — the
    skeleton image lives in ComfyUI's input, so curating the library can't invalidate a
    render that already happened."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM pose_library WHERE id = ?", (pose_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "pose not found")
        conn.execute("DELETE FROM pose_library WHERE id = ?", (pose_id,))
        cleared = conn.execute(
            "UPDATE poses SET pose_library_id = NULL WHERE pose_library_id = ?",
            (pose_id,)).rowcount
    logs.info("process", f"pose '{row['name']}' removed from the library", unlinked=cleared)
    return {"deleted": pose_id, "unlinked_poses": cleared}


@app.post("/api/pose-library/reset")
async def pose_library_reset() -> dict:
    """Top the library up to the shipped starter set — adds what's missing, edits nothing."""
    added = seed_pose_library(force=True)
    return {"restored": added}


@app.get("/api/pose-families")
async def pose_families_get(project_id: int | None = None) -> dict:
    """The families, their members, and which family each axis/tier poses in.

    `project_id` returns that persona's effective map (its overrides layered over the global
    defaults); omitting it returns the global defaults alone.
    """
    seed_axis_families()
    with db.connect() as conn:
        counts = {f: 0 for f in POSE_FAMILIES}
        members: dict[str, list[dict]] = {f: [] for f in POSE_FAMILIES}
        for r in conn.execute("SELECT id, name, family, face_visible FROM pose_library "
                              "ORDER BY family, id"):
            fam = r["family"] or "standing"
            counts[fam] = counts.get(fam, 0) + 1
            members.setdefault(fam, []).append(
                {"id": r["id"], "name": r["name"], "face_visible": bool(r["face_visible"])})
    fam_map = axis_family_map(project_id)
    axes = []
    for group in emotion_map():
        e = fam_map.get(group["axis"], {"default": None, "tiers": {}})
        axes.append({
            "axis": group["axis"], "label": group["label"],
            "family": e["default"],
            # Per-tier, with the effective family resolved so the ramp is readable at a glance.
            "tiers": [{"tier": t["position"], "label": t["label"],
                       "family": e["tiers"].get(t["position"]),
                       "entry_id": e.get("entries", {}).get(t["position"]),
                       "effective": e["tiers"].get(t["position"]) or e["default"]}
                      for t in group["tiers"]],
        })
    return {"families": POSE_FAMILIES, "counts": counts, "members": members,
            "axes": axes, "project_id": project_id}


class AxisFamilyRequest(BaseModel):
    axis: str = Field(min_length=1)
    # None = the axis-wide default; a number targets one rung of the ladder.
    tier: int | None = None
    # '' clears the assignment — for a tier that means "fall back to the axis default".
    family: str = ""
    # Pin this axis/tier to one library entry; None leaves it on the blind family spread.
    entry_id: int | None = None
    # None edits the GLOBAL default; a project id sets that persona's own override, so two
    # characters can hold different postures for the same emotion.
    project_id: int | None = None


@app.post("/api/pose-families")
async def pose_family_set(body: AxisFamilyRequest) -> dict:
    """Assign one axis (or one tier of it) to a posture family."""
    fam = body.family.strip()
    if fam and fam not in POSE_FAMILIES:
        raise HTTPException(400, f"unknown family '{fam}' — one of {', '.join(POSE_FAMILIES)}")
    # A persona's axis-wide override CAN be cleared (it falls back to the global default);
    # the global axis-wide row cannot, or the axis would resolve to nothing.
    if not fam and body.tier is None and body.project_id is None:
        raise HTTPException(400, "the global axis-wide family cannot be cleared, only changed")
    with db.connect() as conn:
        if body.entry_id is not None:
            row = conn.execute("SELECT family FROM pose_library WHERE id = ?",
                               (body.entry_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"pose_library entry {body.entry_id} not found")
            # Pinning an entry from another family would make the two disagree; the entry wins.
            fam = fam or row["family"]
            if row["family"] != fam:
                raise HTTPException(
                    400, f"entry {body.entry_id} is in the '{row['family']}' family, not '{fam}'")
        if not fam:
            conn.execute(
                "DELETE FROM axis_pose_families WHERE axis = ? AND tier IS ? "
                "AND project_id IS ?", (body.axis, body.tier, body.project_id))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO axis_pose_families "
                "(project_id, axis, tier, family, entry_id) VALUES (?, ?, ?, ?, ?)",
                (body.project_id, body.axis, body.tier, fam, body.entry_id))
        empty = not conn.execute(
            "SELECT 1 FROM pose_library WHERE family = ? LIMIT 1", (fam,)).fetchone() if fam else False
    logs.info("process",
              ("pose family: " + ("persona " + str(body.project_id) if body.project_id else "global")
               + f" {body.axis}" + (f" tier {body.tier}" if body.tier is not None else "")
               + (f" -> {fam}" if fam else " -> cleared")))
    return {"axis": body.axis, "tier": body.tier, "family": fam or None,
            "entry_id": body.entry_id, "project_id": body.project_id,
            "warning": f"the '{fam}' family has no library entries yet" if empty else ""}


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

class LoraStackEntry(BaseModel):
    """One concept LoRA overlaid on top of the character/style LoRA (Phase H1b)."""
    lora_name: str
    strength_model: float = Field(default=0.7, ge=0.0, le=2.0)
    strength_clip: float = Field(default=0.7, ge=0.0, le=2.0)
    enabled: bool = True
    triggers: str = ""


class VersionCreate(BaseModel):
    character: str | None = None
    style: str | None = None
    negative: str | None = None
    checkpoint: str | None = None
    seed: int | None = None
    style_lora: str | None = None
    style_lora_strength: float | None = None
    lora_stack: list[LoraStackEntry] | None = None
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
            "style_lora": body.style_lora if body.style_lora is not None else parent["style_lora"],
            "style_lora_strength": (body.style_lora_strength if body.style_lora_strength is not None
                                    else parent["style_lora_strength"]),
            # the concept-LoRA stack rides along with the prompt, so rollback restores it too
            "lora_stack_json": (json.dumps([e.model_dump() for e in body.lora_stack])
                                if body.lora_stack is not None
                                else (parent["lora_stack_json"] or "[]")),
        }
        cur = conn.execute(
            """INSERT INTO prompt_versions
               (project_id, parent_id, character, style, negative, checkpoint, seed,
                style_lora, style_lora_strength, lora_stack_json, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, parent["id"], merged["character"], merged["style"],
                merged["negative"], merged["checkpoint"], merged["seed"],
                merged["style_lora"], merged["style_lora_strength"],
                merged["lora_stack_json"], body.source, body.note,
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


# --------------------------------------------------------------------------- #
# admin / cleanup (0.8.3). Deliberately manual, deliberately narrow.
#
# The version store is append-only by design and the README promises as much. Pruning a
# version is therefore a *deliberate* action with guards, not a general capability: the
# append-only rule exists to prevent **accidental** loss, and these endpoints never fire
# by themselves. Guards: the current version can't go, a signed-off baseline needs an
# explicit override, and children are re-parented so history stays a connected chain
# rather than fragmenting into orphans.
# --------------------------------------------------------------------------- #

@app.delete("/api/versions/{version_id}")
async def version_delete(version_id: int, force: bool = False) -> dict:
    """Prune one prompt version. `force` is required to remove a signed-off baseline."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "version not found")
        project_id = row["project_id"]
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj and proj["current_version_id"] == version_id:
            raise HTTPException(
                409, "that's the current version — roll back to another version first, then delete it")
        n_versions = conn.execute(
            "SELECT COUNT(*) c FROM prompt_versions WHERE project_id = ?", (project_id,)).fetchone()["c"]
        if n_versions <= 1:
            raise HTTPException(409, "a persona must keep at least one version")
        if row["signed_off"] and not force:
            raise HTTPException(
                409, "that version is a signed-off baseline — delete it again with force=true if you're sure")

        # Re-parent children onto this version's parent so the history chain stays intact.
        conn.execute("UPDATE prompt_versions SET parent_id = ? WHERE parent_id = ?",
                     (row["parent_id"], version_id))
        # Images reference the version they were generated from; keep the images, drop the link.
        conn.execute("UPDATE images SET version_id = NULL WHERE version_id = ?", (version_id,))
        conn.execute("DELETE FROM prompt_versions WHERE id = ?", (version_id,))
    logs.info("process", f"version v{version_id} deleted", project_id=project_id,
              was_signed_off=bool(row["signed_off"]) or None)
    _write_persona_sidecar(project_id)
    return {"deleted": version_id, "project_id": project_id,
            "was_signed_off": bool(row["signed_off"])}


@app.delete("/api/projects/{project_id}")
async def project_delete(project_id: int, delete_files: bool = False) -> dict:
    """Delete a persona. `delete_files` also removes its build folder from the share.

    The two are separate on purpose: the build folder holds every rendered image and the
    trained LoRA — often an hour of GPU time — so removing the database record and
    removing that work are different decisions, and the caller has to make both.
    """
    with db.connect() as conn:
        proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, "project not found")
        slug = proj["slug"]
        counts = {
            "versions": conn.execute("SELECT COUNT(*) c FROM prompt_versions WHERE project_id = ?",
                                     (project_id,)).fetchone()["c"],
            "images": conn.execute("SELECT COUNT(*) c FROM images WHERE project_id = ?",
                                   (project_id,)).fetchone()["c"],
            "poses": conn.execute("SELECT COUNT(*) c FROM poses WHERE project_id = ?",
                                  (project_id,)).fetchone()["c"],
        }
        # A running build would have its job row cascade out from under the worker
        # mid-stage. Refuse rather than race it — cancelling is one click away.
        if conn.execute("SELECT 1 FROM jobs WHERE project_id = ? AND status = 'running'",
                        (project_id,)).fetchone():
            raise HTTPException(
                409, "a build is running for this persona — cancel it first, then delete")
        # Clones point at their parent; orphan them rather than cascading the delete into
        # personas the user never asked to remove.
        clones = conn.execute("SELECT COUNT(*) c FROM projects WHERE parent_project_id = ?",
                              (project_id,)).fetchone()["c"]
        conn.execute("UPDATE projects SET parent_project_id = NULL WHERE parent_project_id = ?",
                     (project_id,))
        counts["clones_orphaned"] = clones
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))   # children cascade

    removed_dir = None
    if delete_files:
        folder = BUILDS_ROOT / slug
        try:
            # Guard against a blank/odd slug turning this into "delete the builds root".
            if slug and folder.resolve().parent == BUILDS_ROOT.resolve() and folder.is_dir():
                shutil.rmtree(folder)
                removed_dir = str(folder)
                logs.info("local", f"build folder removed for '{proj['name']}'", path=str(folder))
            else:
                logs.warn("local", "refusing to remove an unexpected build path", path=str(folder))
        except OSError as exc:
            logs.error("local", f"could not remove build folder: {exc}", path=str(folder))
            raise HTTPException(
                500, f"persona deleted, but its build folder could not be removed: {exc}") from exc

    logs.info("process", f"persona '{proj['name']}' deleted", project_id=project_id,
              slug=slug, files_removed=bool(removed_dir), **counts)
    return {"deleted": project_id, "name": proj["name"], "slug": slug,
            "removed_dir": removed_dir, "counts": counts}


@app.delete("/api/projects/{project_id}/lora/{filename}")
async def lora_delete(project_id: int, filename: str) -> dict:
    """Delete one trained LoRA file from the persona's `lora/` folder."""
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    # Path components only — never let a filename escape the project's lora folder.
    if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
        raise HTTPException(400, "invalid LoRA filename")
    target = BUILDS_ROOT / slug / "lora" / filename
    if not target.is_file():
        raise HTTPException(404, f"no such LoRA file: {filename}")
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(500, f"could not delete the LoRA: {exc}") from exc

    cleared = False
    with db.connect() as conn:
        proj = conn.execute("SELECT pose_lora FROM projects WHERE id = ?", (project_id,)).fetchone()
        if proj and proj["pose_lora"] == filename:
            # Otherwise pose renders would keep asking ComfyUI for a file that's gone.
            conn.execute("UPDATE projects SET pose_lora = '' WHERE id = ?", (project_id,))
            cleared = True
    logs.info("process", f"LoRA '{filename}' deleted", project_id=project_id,
              slug=slug, selection_cleared=cleared or None)
    return {"deleted": filename, "selection_cleared": cleared, "loras": _lora_files(slug)}


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
    # Optional external style/detail LoRA for the Studio preview. `None` = fall back to
    # the current version's saved LoRA; "" = force checkpoint-only for this run.
    style_lora: str | None = None
    style_lora_strength: float | None = None
    lora_stack: list[LoraStackEntry] | None = None


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

    # A selected style LoRA or concept stack upgrades base-character ->
    # base-character-lora. Prefer values sent with the request (the Studio previews an
    # unsaved edit); fall back to whatever the saved version carries.
    lora_name = body.style_lora if body.style_lora is not None else version.get("style_lora")
    lora_strength = (body.style_lora_strength if body.style_lora_strength is not None
                     else version.get("style_lora_strength"))
    stack = (_parse_lora_stack([e.model_dump() for e in body.lora_stack])
             if body.lora_stack is not None
             else _parse_lora_stack(version.get("lora_stack_json")))
    await _check_stack_files(stack)
    workflow_id, lora_params, chain = _resolve_style_lora(
        body.workflow, lora_name, lora_strength, stack)
    params.update(lora_params)
    params = _apply_stack_triggers(params, stack)

    logs.info("process", f"generation requested ({workflow_id})",
              project_id=project_id, slug=slug, version_id=version.get("id"),
              lora_stack=len(stack) or None)
    try:
        graph = workflows.build_graph(workflow_id, params, lora_stack=chain)
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


_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                      ".webp": "image/webp", ".gif": "image/gif"}


@app.get("/api/image")
async def proxy_image(filename: str, subfolder: str = "", type: str = "output") -> Any:
    """Serve an image to the browser — from the shared builds mount when we can.

    ComfyUI writes its outputs into /builds, which this container also mounts, so read
    them straight off disk: the dataset, poses and sheets stay browsable while ComfyUI
    is stopped. Fall back to proxying ComfyUI only for images that aren't on the mount —
    type="input"/"temp" live in ComfyUI's own directories.
    """
    if type == "output":
        path = _builds_path(subfolder, filename)
        if path is not None and path.is_file():
            return FileResponse(
                path, media_type=_IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/png"))

    url = comfy.view_url(filename, subfolder, type)
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        logs.warn("integration", f"image not on the builds mount and ComfyUI is unreachable: {exc}",
                  filename=filename, subfolder=subfolder, type=type)
        raise HTTPException(503, "image not on the builds mount and ComfyUI is unreachable") from exc
    if r.status_code != 200:
        raise HTTPException(r.status_code, "image not found")
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


# The framing used on a skeleton-driven candidate (Phase H3c). Deliberately says nothing
# about stance or view: the skeleton already fixes both, and a canned "walking forward,
# mid-stride" against a kneeling skeleton is an outright contradiction rather than the
# harmless redundancy the 0.8.9 measurement covered (that test found stripping stance words
# from an *agreeing* prompt didn't help — it says nothing about a disagreeing one).
DATASET_CN_FRAMING = "full body, full figure visible from head to feet"


def _dataset_is_body(n: int, mode: str = "both") -> bool:
    """Whether candidate `n` is a full-body shot — the half a skeleton may drive (H3c).

    Close-ups are excluded by design: they exist to teach identity at high frequency, and a
    skeleton over a face crop constrains nothing while costing an upload.
    """
    if mode == "faces":
        return False
    if mode == "poses":
        return True
    return n % 2 == 1  # "both" alternates face/body


def _dataset_posed(n: int, mode: str = "both") -> bool:
    """Whether candidate `n` gets a skeleton — every OTHER body shot (Phase H3c).

    Not every body shot: the un-posed ones keep `DATASET_BODY_FRAMINGS`' spread of views and
    camera angles, which the (largely front-facing) pose library cannot supply yet. Posing
    all of them would trade view variety for posture variety instead of gaining both. H3g's
    camera sweep is what eventually removes that tradeoff.
    """
    return _dataset_is_body(n, mode) and (n // 2) % 2 == 1


def _dataset_posed_ordinal(n: int, mode: str = "both") -> int:
    """How many posed candidates precede `n` — its index into the skeleton spread.

    Counted rather than derived from `n` directly because the posed candidates are not
    evenly spaced: in "both" they fall every 4th, in "poses" in adjacent pairs. Indexing on
    `n` gave the pair the same skeleton twice running and covered half the library it
    should have. Both patterns repeat every 4 candidates, so one cycle is enough to count.
    """
    per_cycle = sum(1 for k in range(4) if _dataset_posed(k, mode))
    full, rem = divmod(n, 4)
    return full * per_cycle + sum(1 for k in range(rem) if _dataset_posed(k, mode))


def _dataset_variation(n: int, mode: str = "both", posed: bool = False) -> str:
    """A framing+expression suffix for candidate index `n`, per `mode`:
      - "faces": close-up/bust framings × the full expression spread (top up a weak face).
      - "poses": full-body framings + many views × light expressions (top up weak poses/angles).
      - "both" : alternate a face shot and a body shot (≈50/50), with full expression variety.
    Framing and expression rotate at different rates so pairs vary and don't repeat quickly.

    `posed` (H3c) means a ControlNet skeleton is driving this candidate, so the canned body
    framing is replaced by a stance-neutral one — see `DATASET_CN_FRAMING`.
    """
    if mode == "faces":
        framing = DATASET_FACE_FRAMINGS[n % len(DATASET_FACE_FRAMINGS)]
        return f"{framing}, {DATASET_EXPRESSIONS[n % len(DATASET_EXPRESSIONS)]}"
    if mode == "poses":
        framing = DATASET_CN_FRAMING if posed else DATASET_BODY_FRAMINGS[n % len(DATASET_BODY_FRAMINGS)]
        return f"{framing}, {DATASET_POSE_EXPRESSIONS[n % len(DATASET_POSE_EXPRESSIONS)]}"
    # both — alternate face/body so every batch keeps a strong share of close-ups
    if n % 2 == 0:
        framing = DATASET_FACE_FRAMINGS[(n // 2) % len(DATASET_FACE_FRAMINGS)]
    elif posed:
        framing = DATASET_CN_FRAMING
    else:
        framing = DATASET_BODY_FRAMINGS[(n // 2) % len(DATASET_BODY_FRAMINGS)]
    return f"{framing}, {DATASET_EXPRESSIONS[n % len(DATASET_EXPRESSIONS)]}"


def _dataset_skeleton_spread(conn) -> list[Any]:
    """Library entries ordered so consecutive picks cycle posture FAMILIES, not rows.

    The shipped catalogue is standing-heavy (13 of its 24 entries), so walking it in id or
    name order hands a 30-image batch mostly standing figures — precisely the gap H3c exists
    to close. Round-robining the families keeps a batch spread over standing / crouching /
    kneeling / sitting / lying whatever shape the catalogue grows into.
    """
    rows = conn.execute(
        """SELECT id, name, family, category, keypoints_json, prompt_hint
           FROM pose_library ORDER BY family, name""").fetchall()
    by_family: dict[str, list[Any]] = {}
    for r in rows:
        by_family.setdefault((r["family"] or "standing"), []).append(r)
    out: list[Any] = []
    for i in range(max((len(v) for v in by_family.values()), default=0)):
        for fam in sorted(by_family):
            if i < len(by_family[fam]):
                out.append(by_family[fam][i])
    return out


def _dataset_cn_settings(conn, project_id: int, force: bool = False) -> dict[str, Any] | None:
    """This persona's dataset-ControlNet dials, or None when it is switched off.

    Reuses `pose_controlnet` for the model (one persona, one checkpoint family) but its own
    strength/end, because posing a trained character and teaching an untrained one want
    opposite rigidity — see `_PROJECT_H3C_COLUMNS`.

    `force` serves a per-batch opt-in on a persona that hasn't enabled the setting: the
    stored strength/end are still read rather than re-stating defaults here, so the two
    files can't drift apart.
    """
    proj = conn.execute(
        """SELECT pose_controlnet, dataset_cn_enabled, dataset_cn_strength, dataset_cn_end
           FROM projects WHERE id = ?""", (project_id,)).fetchone()
    if not proj or not (proj["dataset_cn_enabled"] or force):
        return None
    return {
        "controlnet_name": (proj["pose_controlnet"] or "").strip(),
        "strength": proj["dataset_cn_strength"],
        "start_percent": 0.0,
        "end_percent": proj["dataset_cn_end"],
    }


def _version_prompt_params(version: dict[str, Any]) -> dict[str, Any]:
    """The prompt fields shared by preview + dataset generation (seed added by caller)."""
    params = {
        "character": version.get("character") or None,
        "style": version.get("style") or None,
        "negative": version.get("negative") or None,
        "checkpoint": version.get("checkpoint") or None,
    }
    return {k: v for k, v in params.items() if v is not None}


def _parse_lora_stack(raw: Any) -> list[dict[str, Any]]:
    """Read a version's stored stack, keeping only enabled entries that name a file."""
    if not raw:
        return []
    try:
        entries = json.loads(raw) if isinstance(raw, str) else list(raw)
    except (json.JSONDecodeError, TypeError):
        logs.warn("process", "ignoring malformed lora_stack_json on a version")
        return []
    out: list[dict[str, Any]] = []
    for e in entries or []:
        if not isinstance(e, dict) or not (e.get("lora_name") or "").strip():
            continue
        if not e.get("enabled", True):
            continue
        out.append({
            "lora_name": e["lora_name"].strip(),
            "strength_model": float(e.get("strength_model", 0.7)),
            "strength_clip": float(e.get("strength_clip", 0.7)),
            "triggers": (e.get("triggers") or "").strip(),
        })
    return out


def _stack_triggers(stack: list[dict[str, Any]]) -> str:
    """Trigger words for the enabled entries, de-duplicated, order preserved.

    Most concept LoRAs are inert without their trigger tokens, so the stack has to reach
    the prompt as well as the model graph.
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in stack:
        for tok in (t.strip() for t in entry.get("triggers", "").split(",")):
            if tok and tok.lower() not in seen:
                seen.add(tok.lower())
                out.append(tok)
    return ", ".join(out)


def _apply_stack_triggers(params: dict[str, Any], stack: list[dict[str, Any]]) -> dict[str, Any]:
    """Append the stack's trigger words to the style/framing text (all one prompt anyway)."""
    triggers = _stack_triggers(stack)
    if not triggers:
        return params
    out = dict(params)
    style = (out.get("style") or "").strip()
    out["style"] = f"{style}, {triggers}" if style else triggers
    return out


async def _check_stack_files(stack: list[dict[str, Any]]) -> None:
    """Fail fast if a stacked LoRA is no longer on disk.

    Worth the one extra call: without it the run dies inside ComfyUI with a node-level
    error that says nothing about *which* stack entry went missing (a real risk, since a
    stack stores filenames so that curating the library can't break a saved version).
    """
    if not stack:
        return
    try:
        available = set(await comfy.list_models("loras"))
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not verify stacked LoRA files: {exc}")
        return
    missing = [e["lora_name"] for e in stack if e["lora_name"] not in available]
    if missing:
        raise HTTPException(
            400,
            "ComfyUI can no longer see these stacked LoRA file(s): "
            + ", ".join(missing)
            + " — remove them from the stack or restore the files.",
        )


def _resolve_style_lora(
    base_workflow: str,
    lora_name: str | None,
    strength: float | None,
    stack: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Given a Studio-family base workflow, a style LoRA and a concept stack, return the
    workflow to actually run, any single-LoRA params, and the full chain to splice.

    Neither a LoRA nor a stack (or a non-Studio base workflow) is a no-op: the base
    workflow runs unchanged (checkpoint-only). Either one upgrades `base-character` to
    `base-character-lora`, whose `lora_chain` anchor carries the whole chain — the style
    LoRA first, then the concept LoRAs in order, so identity is applied before the
    pose/gesture overlays that sit on top of it.
    """
    name = (lora_name or "").strip()
    stack = stack or []
    if base_workflow != "base-character" or (not name and not stack):
        return base_workflow, {}, []

    s = 1.0 if strength is None else float(strength)
    chain: list[dict[str, Any]] = []
    if name:
        chain.append({"lora_name": name, "strength_model": s, "strength_clip": s})
    chain.extend(stack)

    # The single-LoRA params stay in sync with chain entry 0 so the manifest's declared
    # params and the spliced graph never disagree.
    return "base-character-lora", {
        "lora_name": chain[0]["lora_name"],
        "lora_strength_model": chain[0]["strength_model"],
        "lora_strength_clip": chain[0]["strength_clip"],
    }, chain


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
    # Phase H3c: drive the body shots from pose-library skeletons. None = use the persona's
    # `dataset_cn_enabled` setting; True/False overrides it for this batch only.
    controlnet: bool | None = None


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

    # Carry the version's style LoRA + concept stack into the dataset so the trained
    # character reflects the look chosen in the Studio (upgrades base-character ->
    # base-character-lora). The stack matters most here: a pose/gesture LoRA is exactly
    # how a dataset gains body-language variety the checkpoint alone won't produce.
    ds_stack = _parse_lora_stack(version.get("lora_stack_json"))
    await _check_stack_files(ds_stack)
    ds_workflow, lora_params, ds_chain = _resolve_style_lora(
        "base-character", version.get("style_lora"), version.get("style_lora_strength"), ds_stack)
    base = _apply_stack_triggers({**base, **lora_params}, ds_stack)

    # Continue the variation rotation across successive batches (Generate 30, then +10 more)
    # so coverage stays even instead of restarting at the first framing each time.
    offset = 0
    cn_settings: dict[str, Any] | None = None
    cn_entries: list[Any] = []
    with db.connect() as conn:
        if vary:
            offset = conn.execute(
                "SELECT COUNT(*) n FROM dataset_jobs WHERE project_id = ?", (project_id,),
            ).fetchone()["n"]
        # Phase H3c — teach the LoRA what this character's body DOES. A set built only from
        # standing shots cannot be posed afterwards: render-time ControlNet forces geometry
        # the LoRA has never seen, the two fight, and it lands as melted anatomy
        # (docs/pose-control.md §6.3, measured). Skeletons here are what make the sprite
        # path's skeletons work at all.
        want_cn = body.controlnet
        if want_cn is None:
            want_cn = _dataset_cn_settings(conn, project_id) is not None
        if want_cn and vary:
            # Deliberately NOT seed_pose_library() — an emptied library must stay empty
            # (0.8.5). Re-seeding here would refill a catalogue the user cleared on purpose,
            # and then quietly pose a batch from the starter set they had just deleted.
            # An empty library is refused below instead.
            cn_settings = _dataset_cn_settings(conn, project_id, force=True)
            cn_entries = _dataset_skeleton_spread(conn)

    if cn_settings:
        # Refuse rather than quietly render an unposed batch. 0.8.8 taught this on a single
        # sprite; here the silent version wastes ~30 renders and an hour of GPU, and the
        # result looks fine — it just teaches the LoRA nothing new about the body.
        if not cn_settings["controlnet_name"]:
            raise HTTPException(
                400, "posed dataset shots need a ControlNet model — pick one under "
                     "'Pose structure & face pass' on the Poses tab, or turn off posed "
                     "dataset shots.")
        if not cn_entries:
            raise HTTPException(
                400, "the pose library is empty, so there are no skeletons to build a posed "
                     "dataset from — add entries on the Poses tab or restore the starter set.")
        await _check_controlnet_file(cn_settings["controlnet_name"])
        if body.mode == "faces":
            # Not an error: the user asked for close-ups, and close-ups are CN-free by design.
            logs.warn("process",
                      "posed dataset shots are on but this batch is faces-only — no candidate "
                      "gets a skeleton. Use 'both' or 'poses' to build body coverage.",
                      project_id=project_id)
            cn_settings = None

    mode_desc = {"both": "faces + poses", "faces": "close-up faces + expressions",
                 "poses": "full body + many views"}.get(body.mode, body.mode)
    logs.info("process",
              f"dataset: queuing a batch of {count} "
              + (f"across varied {mode_desc}" if vary else "at fresh seeds (same framing/expression)")
              + (f", every other body shot posed from {len(cn_entries)} skeletons"
                 if cn_settings else ""),
              project_id=project_id, slug=slug)
    queued = 0
    posed = 0
    # One upload per distinct skeleton per batch, not per candidate: a batch is a single
    # burst, so re-uploading the same figure seven times is pure latency. Scoped to this
    # call rather than cached across batches, which keeps the H3b property that an edited
    # library entry always re-stages (a cache keyed on entry id would serve the old figure).
    staged: dict[int, str] = {}
    with db.connect() as conn:
        for i in range(count):
            n = offset + i
            seed = random.randint(1, 2**31 - 1)
            params = {**base, "seed": seed, "output_prefix": f"{slug}/images/ds"}
            variation = None
            cn_cfg: dict[str, Any] | None = None
            entry = None
            if cn_settings and _dataset_posed(n, body.mode):
                entry = cn_entries[_dataset_posed_ordinal(n, body.mode) % len(cn_entries)]
                if entry["id"] not in staged:
                    staged[entry["id"]] = await _stage_family_skeleton(conn, slug, entry)
                cn_cfg = {**cn_settings, "skeleton": staged[entry["id"]],
                          "kind": _controlnet_kind(conn, cn_settings["controlnet_name"])}
            if vary:
                variation = _dataset_variation(n, body.mode, posed=cn_cfg is not None)
                # A skeleton fixes the joints but not what the hands hold or the body rests
                # on; without the hint a "leaning on the wall" figure leans on nothing.
                hint = (entry["prompt_hint"] if entry is not None else "") or ""
                if hint and hint.lower() not in variation.lower():
                    variation = f"{variation}, {hint}"
                params["expression"] = variation
            try:
                graph = workflows.build_graph(ds_workflow, params, lora_stack=ds_chain,
                                              controlnet=cn_cfg)
                logs.verbose("process", f"queuing dataset image {i + 1}/{count}",
                             seed=seed, variation=variation,
                             skeleton=entry["name"] if entry is not None else None)
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
            if cn_cfg:
                posed += 1

    logs.info("process",
              f"dataset: queued {queued} image(s)"
              + (f", {posed} posed from skeletons" if posed else ""),
              project_id=project_id, slug=slug)
    return {"queued": queued, "pose_variety": vary, "mode": body.mode if vary else None,
            "posed": posed, "skeletons": len(staged)}


@app.get("/api/projects/{project_id}/dataset")
async def dataset_list(project_id: int) -> dict:
    await _reconcile_dataset(project_id)
    with db.connect() as conn:
        proj = conn.execute(
            """SELECT dataset_target, pose_controlnet, dataset_cn_enabled,
                      dataset_cn_strength, dataset_cn_end
               FROM projects WHERE id = ?""", (project_id,)).fetchone()
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
        library = conn.execute("SELECT COUNT(*) n FROM pose_library").fetchone()["n"]
    images = [dict(r) for r in rows]
    selected = sum(1 for r in images if r["selected"])
    target = proj["dataset_target"]
    return {
        "target": target,
        "generating": pending > 0,
        "counts": {"candidates": len(images), "selected": selected, "pending": pending},
        "reached": selected >= target,
        "images": images,
        # Phase H3c. `controlnet` + `library` are reported so the toggle can explain itself
        # rather than failing at Generate — both are preconditions the user sets elsewhere
        # (the Poses tab), so the Dataset tab has to say when one is missing.
        "controlnet": {
            "enabled": bool(proj["dataset_cn_enabled"]),
            "model": (proj["pose_controlnet"] or "").strip(),
            "strength": proj["dataset_cn_strength"],
            "end_percent": proj["dataset_cn_end"],
            "library": library,
        },
    }


class DatasetControlNetRequest(BaseModel):
    enabled: bool = False
    # Defaults mirror _PROJECT_H3C_COLUMNS: moderate, and released before the end of the
    # schedule so the last steps resolve a real body rather than the stick figure.
    strength: float = Field(default=0.6, ge=0.0, le=2.0)
    end_percent: float = Field(default=0.7, ge=0.0, le=1.0)


@app.post("/api/projects/{project_id}/dataset-controlnet")
async def set_dataset_controlnet(project_id: int, body: DatasetControlNetRequest) -> dict:
    """Set whether dataset body shots are driven by pose-library skeletons (Phase H3c)."""
    warning = ""
    if body.enabled and body.strength >= 0.9:
        # Allowed — but at sprite-render rigidity the dataset becomes a set of mannequins
        # posed identically to the library, and the LoRA learns the stick figure's habits
        # rather than the character's body.
        warning = ("a dataset ControlNet strength at or above 0.90 renders stiff, "
                   "skeleton-locked bodies — 0.60 is the intended range for teaching "
                   "posture without dictating it")
    with db.connect() as conn:
        cur = conn.execute(
            """UPDATE projects SET dataset_cn_enabled = ?, dataset_cn_strength = ?,
                      dataset_cn_end = ? WHERE id = ?""",
            (1 if body.enabled else 0, body.strength, body.end_percent, project_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "project not found")
        proj = conn.execute("SELECT pose_controlnet FROM projects WHERE id = ?",
                            (project_id,)).fetchone()
    model = (proj["pose_controlnet"] or "").strip()
    if body.enabled and not model:
        warning = warning or ("no ControlNet model is selected yet — pick one under 'Pose "
                              "structure & face pass' on the Poses tab or posed batches "
                              "will be refused")
    logs.info("process", f"dataset ControlNet {'on' if body.enabled else 'off'}",
              project_id=project_id, strength=body.strength, end_percent=body.end_percent)
    return {"enabled": body.enabled, "strength": body.strength,
            "end_percent": body.end_percent, "model": model, "warning": warning}


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
    """Delete one dataset image file from /builds. Best-effort — returns True if a file
    was removed."""
    path = _builds_path(subfolder, filename)
    if path is None:
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


def _lora_files(slug: str) -> list[dict[str, Any]]:
    """Trained LoRA files for a project, **newest first**, each with its build date (the file's
    modified time). The mtime is the reliable 'built on' signal — a rebuild overwrites or adds the
    file and bumps it — so the user can confirm a refresh actually took and which file is latest."""
    lora_dir = BUILDS_ROOT / slug / "lora"
    if not lora_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in lora_dir.glob("*.safetensors"):
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "name": p.name,
            "modified_ts": stat.st_mtime,  # epoch seconds — client formats in the user's locale
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "size": stat.st_size,
        })
    out.sort(key=lambda d: d["modified_ts"], reverse=True)  # newest (freshest build) first
    return out


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
    loras = _lora_files(slug)  # newest first, each with its build (file-modified) date

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


# Measured on the 3090: a 1500-step rank-16 SDXL run peaked at 17.8 GB reserved. Below
# this much FREE VRAM the run dies partway through — usually in the VAE encode — after
# minutes of apparently-healthy progress. Set to 0 to disable the gate.
MIN_TRAIN_VRAM_GB = float(os.getenv("MIN_TRAIN_VRAM_GB", "18"))


async def _require_train_vram() -> None:
    """Refuse to start a long run the card can't finish.

    The GPU is shared. ComfyUI being idle does NOT mean the VRAM is free — Ollama, Immich's
    ML server or another container can be holding a third of the card. ComfyUI's reported
    `vram_free` IS the device-wide figure (measured: it read 17.0 GB free while nvidia-smi
    showed 8.4 GB held by other processes, and 24.9 GB once they released), so it is the
    right number to gate on. Checking here turns a 5-minute run that dies in a torch
    traceback into an immediate, explicit message naming how much is actually free.
    """
    if MIN_TRAIN_VRAM_GB <= 0:
        return
    try:
        info = await comfy.vram()
    except (comfy.ComfyError, httpx.HTTPError) as exc:
        logs.warn("integration", f"could not read VRAM before training: {exc} — starting anyway")
        return

    free_gb, total_gb = info["free"] / 1e9, info["total"] / 1e9
    held_gb = (info["total"] - info["free"] - info["comfy_reserved"]) / 1e9
    logs.info("integration",
              f"VRAM check: {free_gb:.1f} GB free of {total_gb:.1f} GB "
              f"({held_gb:.1f} GB held by other processes)",
              device=info["name"], needed_gb=MIN_TRAIN_VRAM_GB)
    if free_gb >= MIN_TRAIN_VRAM_GB:
        return

    resident = ""
    try:
        models = await ollama.resident()
        if models:
            resident = " Ollama still holds " + ", ".join(
                f"{m['name']} ({m['vram_bytes'] / 1e9:.1f} GB)" for m in models) + "."
    except Exception:  # noqa: BLE001
        pass
    raise TrainNotReady(
        f"not enough free VRAM to train: {free_gb:.1f} GB free of {total_gb:.1f} GB, "
        f"need ~{MIN_TRAIN_VRAM_GB:.0f} GB. Roughly {held_gb:.1f} GB is held by other "
        f"processes on this GPU.{resident} Free the card (or lower MIN_TRAIN_VRAM_GB) "
        f"and start the build again.")


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

    # Training needs VRAM headroom (an OOM here is the #1 failure). Free ComfyUI's models
    # and evict EVERY model Ollama holds — not just ours, since other apps share that
    # Ollama and their model pins the same card.
    logs.info("process", "preparing to train LoRA — freeing VRAM",
              project_id=project_id, trigger=trigger, steps=steps, rank=rank)
    try:
        await ollama.unload_all()
    except ollama.OllamaError:
        pass
    await comfy.free_memory()
    await _require_train_vram()

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

# --------------------------------------------------------------------------- #
# Emotion axes × intensity tiers (Phase H1a). See docs/emotion-depth.md.
#
# Emotion is two dimensions, not one: *which* emotion (axis) and *how much* (tier).
# SillyTavern's 28 labels are the GoEmotions set, and several axes already come graded
# — annoyance→anger, disappointment→sadness→grief — so grouping the 28 by axis yields
# most of the ladder for free. Only the top tiers are new (`custom: True`), and they are
# single lowercase words so `_sprite_stem` exports them verbatim, like the built-in 28.
#
# `graded` marks the axes that are a genuine intensity ladder. The rest (cognition,
# composure, other) are groupings for the grid's benefit — honest about the fact that
# "confusion → surprise" is not an intensity progression. Later enrichment stages should
# only offer "hone the intensity" on graded axes.
#
# Modifiers are prose (project convention) and deliberately describe **posture as well as
# face**: rage and despair are body language, and a face-only repaint can't render them —
# which is the whole reason this pipeline trains a per-character LoRA.
# --------------------------------------------------------------------------- #

# SillyTavern's own 28 expression labels (the GoEmotions set). This is a **fixed external
# contract**, not part of the editable map: it is what ST's classifier can emit and what
# its sprite folder expects. The map may drop or regroup these freely — the flag only
# tells the UI "ST knows this one", so a custom tier can be shown as needing the Phase H2
# state engine to ever fire.
_ST_BUILTIN_28 = frozenset({
    "admiration", "amusement", "anger", "annoyance", "approval", "caring", "confusion",
    "curiosity", "desire", "disappointment", "disapproval", "disgust", "embarrassment",
    "excitement", "fear", "gratitude", "grief", "joy", "love", "nervousness", "neutral",
    "optimism", "pride", "realization", "relief", "remorse", "sadness", "surprise",
})

DEFAULT_EMOTION_AXES: list[dict[str, Any]] = [
    {"axis": "anger", "label": "Anger", "graded": True, "tiers": [
        ("annoyance", "annoyed, slight frown, jaw tight, arms loosely folded, weight shifted to one hip", False),
        ("anger", "angry, furrowed brow, jaw set, shoulders squared, fists at their sides", False),
        ("fury", "furious, shouting, leaning forward aggressively, one fist clenched and raised", True),
    ]},
    {"axis": "fear", "label": "Fear", "graded": True, "tiers": [
        ("nervousness", "nervous, worried eyes, hands fidgeting, shoulders drawn slightly inward", False),
        ("fear", "afraid, wide eyes, leaning back, hands raised defensively", False),
        ("terror", "terrified, screaming, recoiling, arms thrown up to shield the face", True),
    ]},
    {"axis": "sadness", "label": "Sadness", "graded": True, "tiers": [
        ("disappointment", "disappointed, downcast eyes, faint frown, shoulders dropped", False),
        ("sadness", "sad, teary eyes, head lowered, arms hanging limply", False),
        ("grief", "grieving, crying openly, face buried in one hand, body curled inward", False),
        ("despair", "in despair, hollow expression, slumped down, head bowed, arms slack", True),
    ]},
    {"axis": "joy", "label": "Joy", "graded": True, "tiers": [
        ("amusement", "amused, small grin, eyebrows raised, relaxed easy posture", False),
        ("joy", "joyful, bright smile, head up, open welcoming posture", False),
        ("excitement", "excited, beaming, up on their toes, hands raised", False),
        ("elation", "elated, laughing with head thrown back, arms flung wide, mid-celebration", True),
    ]},
    {"axis": "affection", "label": "Affection", "graded": True, "tiers": [
        ("approval", "approving, small satisfied smile, a slight nod", False),
        ("caring", "caring, warm gentle expression, reaching out with one hand", False),
        ("admiration", "admiring, eyes bright and fixed, hands clasped together", False),
        ("love", "loving, soft tender smile, leaning in close", False),
        ("devotion", "devoted, gazing with total sincerity, one hand pressed over their heart", True),
    ]},
    {"axis": "disgust", "label": "Disgust", "graded": True, "tiers": [
        ("disapproval", "disapproving, flat stare, lips pressed thin, arms crossed", False),
        ("disgust", "disgusted, nose wrinkled, lip curled, leaning away", False),
        ("revulsion", "revolted, recoiling hard, a hand clamped over the mouth, turning away", True),
    ]},
    {"axis": "shame", "label": "Shame", "graded": True, "tiers": [
        ("embarrassment", "embarrassed, blushing, glancing away, one hand rubbing the back of the neck", False),
        ("remorse", "remorseful, pained expression, head bowed, hands clasped", False),
        ("humiliation", "humiliated, face burning, curled in on themselves, hiding their face", True),
    ]},
    {"axis": "cognition", "label": "Cognition", "graded": False, "tiers": [
        ("confusion", "confused, brow knitted, head tilted, one hand half-raised in question", False),
        ("curiosity", "curious, eyebrows lifted, leaning in with interest", False),
        ("realization", "struck by a realization, eyes widening, straightening up", False),
        ("surprise", "surprised, eyes wide, mouth open, drawing back sharply", False),
    ]},
    {"axis": "composure", "label": "Composure", "graded": False, "tiers": [
        ("neutral", "neutral expression, calm, relaxed natural stance", False),
        ("relief", "relieved, eyes closed, exhaling, shoulders finally dropping", False),
        ("optimism", "optimistic, hopeful open expression, chin up, looking ahead", False),
        ("pride", "proud, chin raised, confident smile, hands on hips", False),
    ]},
    {"axis": "other", "label": "Other", "graded": False, "tiers": [
        ("desire", "wanting, intent half-lidded gaze, leaning subtly forward", False),
        ("gratitude", "grateful, warm sincere smile, a small bow of the head", False),
    ]},
]


def seed_emotion_map(force: bool = False) -> int:
    """Write the shipped default into the DB. No-op if a map already exists.

    The default is a *starting point*, not the vocabulary: everything below is editable
    through /api/emotion-map, and `force` (the reset action) restores this baseline.
    """
    with db.connect() as conn:
        if not force and conn.execute("SELECT 1 FROM emotion_axes LIMIT 1").fetchone():
            return 0
        conn.execute("DELETE FROM emotion_axes")   # tiers cascade
        n = 0
        for pos, group in enumerate(DEFAULT_EMOTION_AXES, start=1):
            cur = conn.execute(
                "INSERT INTO emotion_axes (axis, label, position, graded) VALUES (?, ?, ?, ?)",
                (group["axis"], group["label"], pos, 1 if group["graded"] else 0),
            )
            axis_id = cur.lastrowid
            for tier, (label, modifier, custom) in enumerate(group["tiers"], start=1):
                # `custom` in the default table is a readability aid; the DB stores the
                # authoritative fact — whether SillyTavern itself knows the label.
                conn.execute(
                    """INSERT INTO emotion_tiers (axis_id, label, position, modifier, builtin)
                       VALUES (?, ?, ?, ?, ?)""",
                    (axis_id, label, tier, modifier, 1 if label in _ST_BUILTIN_28 else 0),
                )
                n += 1
    logs.info("boot", f"emotion map seeded from the shipped default ({n} tiers)", reset=force or None)
    return n


def backfill_pose_axes() -> int:
    """Tag untagged poses with the axis/tier their name matches.

    Poses created before 0.8.2 carry no axis, and so would all pile into 'Ungrouped'.
    Runs on boot and after a map edit; only touches rows whose axis is still blank, so a
    deliberately re-grouped pose is never clobbered.
    """
    idx = expression_index()
    if not idx:
        return 0
    with db.connect() as conn:
        rows = conn.execute("SELECT id, name FROM poses WHERE axis = ''").fetchall()
        n = 0
        for r in rows:
            meta = idx.get((r["name"] or "").strip().lower())
            if not meta:
                continue
            conn.execute("UPDATE poses SET axis = ?, tier = ? WHERE id = ?",
                         (meta["axis"], meta["tier"], r["id"]))
            n += 1
    if n:
        logs.info("boot", f"tagged {n} existing pose(s) with their emotion axis")
    return n


def emotion_map() -> list[dict[str, Any]]:
    """The current map, nested axes -> tiers, in display order."""
    with db.connect() as conn:
        axes = conn.execute("SELECT * FROM emotion_axes ORDER BY position, id").fetchall()
        tiers = conn.execute("SELECT * FROM emotion_tiers ORDER BY position, id").fetchall()
    by_axis: dict[int, list[dict]] = {}
    for t in tiers:
        row = dict(t)
        row["builtin"] = bool(row["builtin"])
        row["custom"] = not row["builtin"]   # derived, never stored
        by_axis.setdefault(t["axis_id"], []).append(row)
    return [{**dict(a), "graded": bool(a["graded"]), "tiers": by_axis.get(a["id"], [])} for a in axes]


def expression_index() -> dict[str, dict[str, Any]]:
    """label -> {axis, tier, modifier, custom} — the lookup used when a pose is created."""
    out: dict[str, dict[str, Any]] = {}
    for group in emotion_map():
        for t in group["tiers"]:
            out[t["label"]] = {
                "axis": group["axis"], "tier": t["position"],
                "modifier": t["modifier"], "custom": bool(t["custom"]),
            }
    return out


def expression_labels(include_custom: bool = True) -> list[str]:
    """Every sprite label in the map, in axis/tier order.

    `include_custom=False` gives the SillyTavern built-ins only — the labels ST's own
    classifier can actually emit. Custom tiers (fury, terror, …) need the Phase H2 state
    engine or a manual trigger to ever show up, so the two lists are not interchangeable.
    """
    return [t["label"] for g in emotion_map() for t in g["tiers"]
            if include_custom or not t["custom"]]


def presets() -> dict[str, list[tuple[str, str]]]:
    """Pose presets. Built from the DB each call so an edited map takes effect at once."""
    idx = expression_index()
    return {
        "starter": STARTER_POSES,
        "expressions": [(e.capitalize(), idx[e]["modifier"]) for e in expression_labels(False)],
        "expressions-tiered": [(e.capitalize(), idx[e]["modifier"]) for e in expression_labels(True)],
    }


def _pose_dict(row: Any) -> dict:
    d = dict(row)
    return d


async def _reconcile_poses(project_id: int) -> None:
    """Advance in-flight pose renders, across both passes (Phase H3).

    `pending` is pass 1 (body, ControlNet-conditioned) and `facepass` is pass 2. A
    finished pass 1 stores its image as the **base** and — if the face pass is on — queues
    pass 2 against it rather than declaring the pose done. The base is kept either way,
    because it is what makes a face re-roll cheap and what the before/after comparison
    shows (docs/pose-control.md §4.1).
    """
    with db.connect() as conn:
        pending = conn.execute(
            "SELECT * FROM poses WHERE project_id = ? AND status IN ('pending', 'facepass') "
            "AND prompt_id != ''", (project_id,),
        ).fetchall()
    if not pending:
        return
    logs.verbose("process", f"reconciling {len(pending)} in-flight pose render(s)",
                 project_id=project_id)
    try:
        hist = await comfy.history_all()
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not read history to reconcile poses: {exc}")
        return

    slug = await _project_slug(project_id)
    version = None
    done = failed = queued_face = 0
    with db.connect() as conn:
        lora_cfg = _pose_lora_cfg(conn, project_id, slug) if slug else None
        for job in pending:
            entry = hist.get(job["prompt_id"])
            if not entry:
                continue
            st = comfy.status_of(entry)
            if st == "error":
                conn.execute("UPDATE poses SET status = 'error' WHERE id = ?", (job["id"],))
                failed += 1
                logs.warn("process", f"a pose {job['status']} render failed", pose_id=job["id"])
                continue
            if st != "success":
                continue
            imgs = comfy.outputs_from(entry)
            if not imgs:
                continue
            out = imgs[-1]

            if job["status"] == "facepass":
                conn.execute(
                    "UPDATE poses SET filename = ?, subfolder = ?, status = 'done' WHERE id = ?",
                    (out["filename"], out["subfolder"], job["id"]))
                done += 1
                logs.verbose("process", "face pass finished", pose_id=job["id"],
                             file=out["filename"])
                continue

            # pass 1 finished — record the base, then decide whether pass 2 runs
            conn.execute(
                "UPDATE poses SET base_filename = ?, base_subfolder = ?, filename = ?, "
                "subfolder = ? WHERE id = ?",
                (out["filename"], out["subfolder"], out["filename"], out["subfolder"], job["id"]))
            face_cfg = _pose_face_cfg(conn, project_id, job,
                                      _pose_library_entry(conn, project_id, job))
            started = False
            if face_cfg and slug:
                if version is None:
                    version = await _current_version(project_id)
                if version:
                    row = conn.execute("SELECT * FROM poses WHERE id = ?", (job["id"],)).fetchone()
                    try:
                        started = await _queue_face_pass(conn, project_id, slug, version,
                                                         row, lora_cfg, face_cfg)
                    except (workflows.WorkflowError, comfy.ComfyError) as exc:
                        logs.error("process", f"could not queue the face pass: {exc}",
                                   pose_id=job["id"])
            if started:
                queued_face += 1
            else:
                conn.execute("UPDATE poses SET status = 'done' WHERE id = ?", (job["id"],))
                done += 1
                logs.verbose("process", "pose render finished", pose_id=job["id"],
                             file=out["filename"])
    if done or failed or queued_face:
        logs.info("process",
                  f"pose reconcile: {done} finished, {queued_face} into the face pass, "
                  f"{failed} failed", project_id=project_id)


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


async def _current_version(project_id: int) -> dict[str, Any] | None:
    """The project's current prompt version, read straight from the DB.

    Deliberately not `get_project()`: the reconciler needs this while advancing poses, and
    going through the endpoint would pull in work it doesn't need.
    """
    with db.connect() as conn:
        proj = conn.execute("SELECT current_version_id FROM projects WHERE id = ?",
                            (project_id,)).fetchone()
        if not proj or not proj["current_version_id"]:
            return None
        row = conn.execute("SELECT * FROM prompt_versions WHERE id = ?",
                           (proj["current_version_id"],)).fetchone()
    return dict(row) if row else None


def _pose_seed(version_seed: int, pose_id: int) -> int:
    """A per-pose seed derived from the version's.

    Before Phase H3 every pose in a set rendered at the version's single seed, so the
    whole set came back as the same picture with a different expression suffix — the
    largest single cause of "the poses all look the same" (docs/pose-control.md §0a).
    Derived rather than random so a set stays reproducible; Python's `hash()` is salted
    per process and would not survive a restart.
    """
    mixed = (int(version_seed) * 2654435761 + int(pose_id) * 40503 + 0x9E3779B9) & 0xFFFFFFFF
    return mixed % 2_147_483_647


def _controlnet_kind(conn, filename: str) -> str:
    """The registry's `kind` for a ControlNet file, defaulting to plain openpose.

    It decides whether `apply_controlnet` inserts `SetUnionControlNetType`, so an
    unregistered union model renders without ever being told what to control.
    """
    reg = conn.execute("SELECT kind FROM controlnets WHERE filename = ?", (filename,)).fetchone()
    return (reg["kind"] if reg else "openpose")


def _pose_cn_cfg(conn, project_id: int, pose_row: Any,
                 skeleton_override: str | None = None) -> dict[str, Any] | None:
    """Resolve the ControlNet config for one pose, or None to render prompt-only.

    Per-pose values win over the persona's defaults; NULL means inherit, so moving the
    persona-level dial moves every pose that hasn't been individually overridden.
    """
    proj = conn.execute(
        """SELECT pose_controlnet, pose_cn_strength, pose_cn_start, pose_cn_end, pose_skeleton
           FROM projects WHERE id = ?""", (project_id,)).fetchone()
    if not proj:
        return None
    cn_name = (proj["pose_controlnet"] or "").strip()
    skeleton = ((pose_row["skeleton_ref"] or "").strip()
                or (proj["pose_skeleton"] or "").strip()
                or (skeleton_override or "").strip())
    if skeleton and not cn_name:
        # The user picked a figure and is expecting it to be obeyed. Rendering anyway with
        # no ControlNet produces a perfectly good image that ignores the skeleton entirely —
        # which reads as "the re-render did nothing" with no error anywhere. Say it loudly.
        logs.warn("process",
                  "a skeleton is set but NO ControlNet model is selected — this pose renders "
                  "WITHOUT structural control and the chosen figure is ignored. Pick a model "
                  "under Pose structure & face pass.",
                  project_id=project_id, pose_id=pose_row["id"], skeleton=skeleton)
        return None
    if not cn_name or not skeleton:
        return None
    strength = pose_row["cn_strength"]
    return {
        "controlnet_name": cn_name,
        "skeleton": skeleton,
        "kind": _controlnet_kind(conn, cn_name),
        "strength": proj["pose_cn_strength"] if strength is None else strength,
        "start_percent": proj["pose_cn_start"],
        "end_percent": proj["pose_cn_end"],
    }


def _pose_library_entry(conn, project_id: int, pose_row: Any) -> dict[str, Any] | None:
    """The library entry behind this pose's skeleton — per-pose first, else the persona's."""
    lib_id = pose_row["pose_library_id"]
    if lib_id is None:
        proj = conn.execute("SELECT pose_library_id FROM projects WHERE id = ?",
                            (project_id,)).fetchone()
        lib_id = proj["pose_library_id"] if proj else None
    if lib_id is None:
        return None
    row = conn.execute("SELECT * FROM pose_library WHERE id = ?", (lib_id,)).fetchone()
    return _pose_lib_dict(row) if row else None


def _pose_face_cfg(conn, project_id: int, pose_row: Any,
                   entry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Face-pass settings for one pose, or None when the pass is switched off.

    An explicit per-pose setting always wins. Otherwise a skeleton that hides the face
    (`face_visible = false` — head down, covering face) turns the pass off by default:
    FaceDetailer cannot find a face that isn't there, and forcing it repaints a hand into
    something mangled. The default should not fight the pose.
    """
    proj = conn.execute(
        "SELECT pose_face_pass, pose_face_denoise FROM projects WHERE id = ?",
        (project_id,)).fetchone()
    if not proj:
        return None
    enabled = pose_row["face_pass"]
    if enabled is None:
        if entry is not None and not entry.get("face_visible", True):
            return None
        enabled = proj["pose_face_pass"]
    if enabled != 1:
        return None
    denoise = pose_row["face_denoise"]
    return {"denoise": proj["pose_face_denoise"] if denoise is None else denoise}


async def _check_controlnet_file(name: str) -> None:
    """Fail with the filename rather than letting ComfyUI throw a node-level error."""
    try:
        available = set(await comfy.list_models("controlnet"))
    except Exception as exc:  # noqa: BLE001
        logs.warn("integration", f"could not verify the ControlNet file: {exc}")
        return
    if name not in available:
        raise HTTPException(400, f"ComfyUI cannot see the ControlNet '{name}' — check it is "
                                 f"in models/controlnet and restart ComfyUI")


async def _queue_pose(conn, project_id: int, slug: str, version: dict, pose_row: Any,
                      lora_cfg: dict | None = None) -> None:
    """Submit one pose's render and mark it pending.

    With a resolved `lora_cfg` the render goes through `pose-with-lora` (character LoRA
    loaded, trigger prepended); otherwise it uses the LoRA-free `base-character` graph.
    Either way the version's concept-LoRA stack is chained on top (Phase H1b) — this is
    the path that most wants it, since a pose the character LoRA never learned is exactly
    what a gesture/pose LoRA is for.
    """
    seed = pose_row["seed"] or _pose_seed(version.get("seed") or 0, pose_row["id"])
    entry = _pose_library_entry(conn, project_id, pose_row)
    # Nothing chosen explicitly? Let the pose's emotion axis/tier pick from its family.
    fam = None
    if not (pose_row["skeleton_ref"] or "").strip():
        fam = await _resolve_family_skeleton(conn, slug, pose_row, project_id)
        if fam and entry is None:
            entry = dict(fam["entry"])
    # A skeleton fixes where the limbs go but says nothing about what they are doing —
    # "holding a sword" is joint positions plus an object that only the prompt can supply.
    # Without this the figure grips thin air (docs/pose-control.md §3.6).
    modifier = pose_row["modifier"] or ""
    hint = (entry or {}).get("prompt_hint") or ""
    if hint and hint.lower() not in modifier.lower():
        modifier = f"{modifier}, {hint}" if modifier else hint
    params = {
        **_version_prompt_params(version),
        "seed": seed,
        "expression": modifier or None,
        "output_prefix": f"{slug}/images/pose_{pose_row['id']}",
    }
    stack = _parse_lora_stack(version.get("lora_stack_json"))
    chain = stack
    if lora_cfg:
        workflow_id = "pose-with-lora"
        params["lora_name"] = lora_cfg["lora_name"]
        params["lora_strength"] = lora_cfg["lora_strength"]
        params["trigger"] = lora_cfg["trigger"]
    else:
        # No character LoRA: base-character has no chain anchor, so fall back to the
        # Studio graph, which does — the stack still applies.
        workflow_id, lora_params, chain = _resolve_style_lora(
            "base-character", version.get("style_lora"), version.get("style_lora_strength"), stack)
        params.update(lora_params)
    params = _apply_stack_triggers(params, stack)
    params = {k: v for k, v in params.items() if v is not None}
    cn_cfg = _pose_cn_cfg(conn, project_id, pose_row, fam["ref"] if fam else None)
    logs.verbose("process", "queuing pose render", pose_id=pose_row["id"], name=pose_row["name"],
                 workflow=workflow_id, lora=lora_cfg["lora_name"] if lora_cfg else None,
                 lora_stack=len(stack) or None, seed=seed,
                 controlnet=cn_cfg["controlnet_name"] if cn_cfg else None,
                 family=fam["family"] if fam else None,
                 family_entry=fam["entry"]["name"] if fam else None)
    graph = workflows.build_graph(workflow_id, params, lora_stack=chain, controlnet=cn_cfg)
    prompt_id = await comfy.submit(graph)
    # Pass 1 clears any previous pass-1 output: a re-render invalidates the stored base,
    # and leaving a stale one would let a face re-roll silently work off the old body.
    conn.execute(
        "UPDATE poses SET prompt_id = ?, status = 'pending', seed = ?, base_filename = '', "
        "base_subfolder = '' WHERE id = ?",
        (prompt_id, seed, pose_row["id"]))


async def _queue_face_pass(conn, project_id: int, slug: str, version: dict, pose_row: Any,
                           lora_cfg: dict | None, face_cfg: dict) -> bool:
    """Pass 2: re-render just the face of a finished pass-1 image.

    Reads the stored base off the builds share and stages it into ComfyUI's input the same
    way the sprite export does, so this needs no extra mount. Returns False (leaving the
    pose on its pass-1 image) if the base can't be read — a missing face pass is a much
    better outcome than losing the pose.
    """
    src = BUILDS_ROOT / (pose_row["base_subfolder"] or f"{slug}/images") / pose_row["base_filename"]
    try:
        data = src.read_bytes()
    except OSError as exc:
        logs.warn("local", f"cannot read the base render for the face pass: {exc}", path=str(src))
        return False
    up = await comfy.upload_image(data, f"pose_{pose_row['id']}_base.png", f"pf-facepass-{slug}")
    img_ref = f"{up['subfolder']}/{up['name']}" if up.get("subfolder") else up["name"]

    params = {
        "image": img_ref,
        "character": version.get("character") or None,
        "negative": version.get("negative") or None,
        "checkpoint": version.get("checkpoint") or None,
        "expression": pose_row["modifier"] or None,
        "denoise": face_cfg["denoise"],
        "seed": (pose_row["seed"] or 0) + 1,
        "output_prefix": f"{slug}/images/pose_{pose_row['id']}_face",
    }
    if lora_cfg:
        params["lora_name"] = lora_cfg["lora_name"]
        params["lora_strength"] = lora_cfg["lora_strength"]
        params["trigger"] = lora_cfg["trigger"]
    params = {k: v for k, v in params.items() if v is not None}

    graph = workflows.build_graph("pose-face-pass", params)
    if not lora_cfg:
        workflows.bypass_optional_lora(graph, workflows.get_manifest("pose-face-pass"))
    logs.verbose("process", "queuing face pass", pose_id=pose_row["id"], name=pose_row["name"],
                 denoise=face_cfg["denoise"], base=pose_row["base_filename"])
    prompt_id = await comfy.submit(graph)
    conn.execute("UPDATE poses SET prompt_id = ?, status = 'facepass' WHERE id = ?",
                 (prompt_id, pose_row["id"]))
    return True


async def _stage_family_skeleton(conn, slug: str, entry: Any,
                                 width: int = 832, height: int = 1216) -> str:
    """Render a library entry and push it into ComfyUI's input, returning its ref.

    Re-uploaded (overwrite) on every render rather than cached by name: the staged PNG is
    derived from keypoints the user can edit, and a cache keyed on the entry id would keep
    serving the pre-edit figure with nothing to show why.
    """
    png = skeleton.render_png(json.loads(entry["keypoints_json"]), width, height)
    up = await comfy.upload_image(png, f"{slug}_fam_{entry['id']}.png", f"pf-skeletons-{slug}")
    return f"{up['subfolder']}/{up['name']}" if up.get("subfolder") else up["name"]


async def _resolve_family_skeleton(conn, slug: str, pose_row: Any,
                                   project_id: int | None = None) -> dict | None:
    """The family-assigned figure for this pose, staged and ready — or None.

    Only consulted when the pose has no skeleton of its own and the persona has no default:
    an explicit choice always outranks a family, or assigning a family would silently
    overwrite per-pose work.
    """
    family, pinned = _family_for(pose_row["axis"], pose_row["tier"], project_id)
    if not family:
        return None
    entry = None
    if pinned is not None:
        entry = conn.execute(
            "SELECT id, name, keypoints_json, face_visible, prompt_hint FROM pose_library "
            "WHERE id = ?", (pinned,)).fetchone()
    if entry is None:
        entry = _family_pick(conn, family, pose_row["name"] or "")
    if entry is None:
        logs.warn("process", f"pose family '{family}' has no library entries — "
                            "this pose renders without structural control",
                  pose_id=pose_row["id"], axis=pose_row["axis"])
        return None
    ref = await _stage_family_skeleton(conn, slug, entry)
    logs.verbose("process", "pose family resolved a skeleton", pose_id=pose_row["id"],
                 axis=pose_row["axis"], tier=pose_row["tier"], family=family,
                 entry=entry["name"])
    return {"ref": ref, "entry": entry, "family": family}


def _annotate_skeletons(conn, project_id: int, rows: list, poses: list[dict]) -> None:
    """Tag each pose with the skeleton it will actually render from, and where that came from.

    Which figure a pose uses is otherwise invisible in the grid — it's a per-pose override
    falling back to a persona default, so "why is this one different?" can only be answered
    by opening the pose. `skeleton_source` distinguishes the three states that matter:
    `pose` (overridden here), `persona` (inherited), `custom` (uploaded, not a library entry).
    """
    proj = conn.execute(
        "SELECT pose_library_id, pose_skeleton FROM projects WHERE id = ?",
        (project_id,)).fetchone()
    default_id = proj["pose_library_id"] if proj else None
    default_skel = (proj["pose_skeleton"] or "").strip() if proj else ""

    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM pose_library")}
    default_name = names.get(default_id) if default_id is not None else None

    for row, p in zip(rows, poses):
        own_ref = (row["skeleton_ref"] or "").strip()
        own_id = row["pose_library_id"]
        if own_ref:
            p["skeleton_name"] = names.get(own_id) if own_id is not None else "custom upload"
            p["skeleton_source"] = "pose" if own_id is not None else "custom"
        elif default_skel:
            p["skeleton_name"] = default_name or "custom upload"
            p["skeleton_source"] = "persona"
        else:
            p["skeleton_name"], p["skeleton_source"] = None, "none"


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
        _annotate_skeletons(conn, project_id, rows, poses)
    # 'facepass' is pass 2 — still in flight, so it has to keep the UI polling.
    pending = sum(1 for p in poses if p["status"] in ("pending", "facepass"))

    # The map is authoritative, the stored axis/tier only a fallback: resolve each pose
    # against the *current* map by name, so renaming an axis or reordering an intensity
    # ladder re-groups the grid at once instead of leaving it on the values captured when
    # the pose was created. Poses whose label has since left the map keep their last
    # known grouping rather than silently jumping to "Ungrouped".
    idx = expression_index()
    for p in poses:
        meta = idx.get((p["name"] or "").strip().lower())
        if meta:
            p["axis"], p["tier"] = meta["axis"], meta["tier"]

    # Axis metadata + per-axis completion. This is what makes a weak axis visible: the
    # baseline review is supposed to answer "which emotion is this persona bad at?", and
    # an alphabetical grid can't.
    groups = [{"axis": a["axis"], "label": a["label"], "position": a["position"],
               "graded": a["graded"], "total": 0, "done": 0}
              for a in emotion_map()]
    by_axis = {g["axis"]: g for g in groups}
    ungrouped = {"axis": "", "label": "Ungrouped", "position": 9999,
                 "graded": False, "total": 0, "done": 0}
    for p in poses:
        g = by_axis.get(p.get("axis") or "", ungrouped)
        g["total"] += 1
        if p["status"] == "done":
            g["done"] += 1
    if ungrouped["total"]:
        groups.append(ungrouped)

    return {"poses": poses, "generating": pending > 0,
            "axes": [g for g in groups if g["total"]],
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
        # If the name matches a label in the emotion map, inherit its axis/tier so the
        # pose lands in the right group — and its modifier, when none was supplied.
        meta = expression_index().get(body.name.strip().lower(), {})
        cur = conn.execute(
            "INSERT INTO poses (project_id, name, modifier, position, axis, tier) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, body.name.strip(), body.modifier.strip() or meta.get("modifier", ""),
             pos, meta.get("axis", ""), meta.get("tier", 0)),
        )
        row = conn.execute("SELECT * FROM poses WHERE id = ?", (cur.lastrowid,)).fetchone()
    logs.info("process", f"pose added: {body.name}", project_id=project_id, pose_id=cur.lastrowid)
    return _pose_dict(row)


# --------------------------------------------------------------------------- #
# emotion map CRUD (Phase H1a) — the shipped default is a starting point, not the
# vocabulary. Axes and tiers are both editable: rename, reorder, add, delete, and
# rewrite the prose modifier that drives the render.
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_")


class AxisIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    axis: str | None = None            # defaults to a slug of the label
    graded: bool = True


class AxisPatch(BaseModel):
    label: str | None = None
    graded: bool | None = None
    position: int | None = None


class TierIn(BaseModel):
    axis_id: int
    label: str = Field(min_length=1, max_length=40)
    modifier: str = ""
    position: int | None = None        # defaults to the end of the axis


class TierPatch(BaseModel):
    label: str | None = None
    modifier: str | None = None
    position: int | None = None
    axis_id: int | None = None         # move a tier to a different axis


@app.get("/api/emotion-map")
async def emotion_map_get() -> dict:
    m = emotion_map()
    return {
        "axes": m,
        "counts": {
            "axes": len(m),
            "tiers": sum(len(a["tiers"]) for a in m),
            "custom": sum(1 for a in m for t in a["tiers"] if t["custom"]),
        },
    }


@app.post("/api/emotion-map/reset")
async def emotion_map_reset() -> dict:
    """Restore the shipped default, discarding every edit. Poses already created are
    untouched — this only changes what future presets offer and how the grid groups."""
    n = seed_emotion_map(force=True)
    return {"reset": True, "tiers": n, "axes": emotion_map()}


@app.post("/api/emotion-map/axes", status_code=201)
async def axis_add(body: AxisIn) -> dict:
    axis = _slug(body.axis or body.label)
    if not axis:
        raise HTTPException(400, "axis name must contain a letter or digit")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM emotion_axes WHERE axis = ?", (axis,)).fetchone():
            raise HTTPException(409, f"an axis '{axis}' already exists")
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 m FROM emotion_axes").fetchone()["m"]
        conn.execute("INSERT INTO emotion_axes (axis, label, position, graded) VALUES (?, ?, ?, ?)",
                     (axis, body.label.strip(), pos, 1 if body.graded else 0))
    logs.info("process", f"emotion axis '{body.label}' added")
    return {"axes": emotion_map()}


@app.patch("/api/emotion-map/axes/{axis_id}")
async def axis_update(axis_id: int, body: AxisPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "graded" in fields:
        fields["graded"] = 1 if fields["graded"] else 0
    if not fields:
        raise HTTPException(400, "nothing to update")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM emotion_axes WHERE id = ?", (axis_id,)).fetchone() is None:
            raise HTTPException(404, "axis not found")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE emotion_axes SET {sets} WHERE id = ?", (*fields.values(), axis_id))
    return {"axes": emotion_map()}


@app.delete("/api/emotion-map/axes/{axis_id}")
async def axis_delete(axis_id: int) -> dict:
    """Remove an axis and its tiers. Existing poses keep their rendered images and their
    `axis` string — they just group under 'Ungrouped' from here on."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM emotion_axes WHERE id = ?", (axis_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "axis not found")
        n = conn.execute("SELECT COUNT(*) c FROM emotion_tiers WHERE axis_id = ?", (axis_id,)).fetchone()["c"]
        conn.execute("DELETE FROM emotion_axes WHERE id = ?", (axis_id,))
    logs.info("process", f"emotion axis '{row['label']}' deleted", tiers_removed=n)
    return {"deleted": axis_id, "tiers_removed": n, "axes": emotion_map()}


@app.post("/api/emotion-map/tiers", status_code=201)
async def tier_add(body: TierIn) -> dict:
    # the label becomes a sprite filename, so normalise it to a clean lowercase token
    label = _slug(body.label)
    if not label:
        raise HTTPException(400, "tier label must contain a letter or digit")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM emotion_axes WHERE id = ?", (body.axis_id,)).fetchone() is None:
            raise HTTPException(404, "axis not found")
        if conn.execute("SELECT 1 FROM emotion_tiers WHERE label = ?", (label,)).fetchone():
            raise HTTPException(409, f"a tier '{label}' already exists — labels are sprite filenames")
        pos = body.position or conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 m FROM emotion_tiers WHERE axis_id = ?",
            (body.axis_id,)).fetchone()["m"]
        conn.execute(
            """INSERT INTO emotion_tiers (axis_id, label, position, modifier, builtin)
               VALUES (?, ?, ?, ?, ?)""",
            (body.axis_id, label, pos, body.modifier.strip(),
             1 if label in _ST_BUILTIN_28 else 0))
    logs.info("process", f"emotion tier '{label}' added")
    return {"axes": emotion_map()}


@app.patch("/api/emotion-map/tiers/{tier_id}")
async def tier_update(tier_id: int, body: TierPatch) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "label" in fields:
        fields["label"] = _slug(fields["label"])
        if not fields["label"]:
            raise HTTPException(400, "tier label must contain a letter or digit")
        fields["builtin"] = 1 if fields["label"] in _ST_BUILTIN_28 else 0
    if not fields:
        raise HTTPException(400, "nothing to update")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM emotion_tiers WHERE id = ?", (tier_id,)).fetchone() is None:
            raise HTTPException(404, "tier not found")
        if "label" in fields and conn.execute(
                "SELECT 1 FROM emotion_tiers WHERE label = ? AND id != ?",
                (fields["label"], tier_id)).fetchone():
            raise HTTPException(409, f"a tier '{fields['label']}' already exists")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE emotion_tiers SET {sets} WHERE id = ?", (*fields.values(), tier_id))
    return {"axes": emotion_map()}


@app.post("/api/emotion-map/tiers/{tier_id}/move")
async def tier_move(tier_id: int, direction: str = "up") -> dict:
    """Swap a tier with its neighbour, then renumber the axis 1..N.

    A dedicated swap rather than a raw `position` write: setting a position directly
    creates ties (two tiers claiming 2), and the resulting order then depends on row id,
    which is not what "move up" should mean on an intensity ladder.
    """
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM emotion_tiers WHERE id = ?", (tier_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "tier not found")
        siblings = conn.execute(
            "SELECT id FROM emotion_tiers WHERE axis_id = ? ORDER BY position, id",
            (row["axis_id"],)).fetchall()
        ids = [r["id"] for r in siblings]
        i = ids.index(tier_id)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for pos, tid in enumerate(ids, start=1):
            conn.execute("UPDATE emotion_tiers SET position = ? WHERE id = ?", (pos, tid))
    return {"axes": emotion_map()}


@app.delete("/api/emotion-map/tiers/{tier_id}")
async def tier_delete(tier_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM emotion_tiers WHERE id = ?", (tier_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "tier not found")
        conn.execute("DELETE FROM emotion_tiers WHERE id = ?", (tier_id,))
    logs.info("process", f"emotion tier '{row['label']}' deleted",
              was_builtin=bool(row["builtin"]) or None)
    return {"deleted": tier_id, "was_builtin": bool(row["builtin"]), "axes": emotion_map()}


class PresetRequest(BaseModel):
    preset: str = "starter"


def _apply_preset(project_id: int, preset: str) -> int:
    """Add a preset's poses that don't already exist. Returns how many were added.
    Shared by the endpoint and the `lora_build` job handler. Raises on unknown preset /
    missing project."""
    all_presets = presets()
    items = all_presets.get(preset)
    if not items:
        raise HTTPException(422, f"unknown preset '{preset}' (have: {list(all_presets)})")
    idx = expression_index()
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
            meta = idx.get(name.lower(), {})
            conn.execute(
                """INSERT INTO poses (project_id, name, modifier, position, axis, tier)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, name, modifier, base + i,
                 meta.get("axis", ""), meta.get("tier", 0)))
            added += 1
    logs.info("process", f"pose preset '{preset}' applied: +{added}", project_id=project_id)
    return added


@app.post("/api/projects/{project_id}/poses/preset")
async def pose_preset(project_id: int, body: PresetRequest) -> dict:
    return {"added": _apply_preset(project_id, body.preset), "preset": body.preset}


class PoseUpdate(BaseModel):
    name: str | None = None
    modifier: str | None = None
    # '' clears the per-pose skeleton, falling back to the persona's default (Phase H3b).
    skeleton_ref: str | None = None


@app.patch("/api/projects/{project_id}/poses/{pose_id}")
async def pose_update(project_id: int, pose_id: int, body: PoseUpdate) -> dict:
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = ?"); vals.append(body.name.strip())
    if body.modifier is not None:
        sets.append("modifier = ?"); vals.append(body.modifier.strip())
    if body.skeleton_ref is not None:
        ref = body.skeleton_ref.strip()
        sets.append("skeleton_ref = ?"); vals.append(ref)
        # Clearing the skeleton clears its provenance too, or the grid would keep claiming
        # the pose came from a library entry it no longer uses.
        if not ref:
            sets.append("pose_library_id = NULL")
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
            """SELECT trigger_word, train_status, pose_lora, pose_lora_strength,
                      pose_controlnet, pose_cn_strength, pose_cn_start, pose_cn_end,
                      pose_skeleton, pose_face_pass, pose_face_denoise
               FROM projects WHERE id = ?""",
            (project_id,),
        ).fetchone()
    if proj is None:
        raise HTTPException(404, "project not found")

    files = _lora_files(slug)  # newest first, with build dates
    try:
        comfy_opts = set(await comfy.list_models("loras"))
    except Exception:  # noqa: BLE001
        comfy_opts = set()

    def visible(fn: str) -> bool:
        return f"{slug}/lora/{fn}" in comfy_opts

    return {
        "trigger_word": (proj["trigger_word"] or "") or default_trigger(slug),
        "train_status": proj["train_status"],
        "loras": [{**f, "comfy_visible": visible(f["name"])} for f in files],
        "selected": (proj["pose_lora"] or "").strip(),
        "strength": proj["pose_lora_strength"] or 1.0,
        # a trained LoRA exists on disk but ComfyUI can't see any of them → the user still
        # needs `loras: /builds` in extra_model_paths.yaml + a ComfyUI restart.
        "needs_extra_paths": bool(files) and not any(visible(f["name"]) for f in files),
        # Phase H3: structural pose control + the face pass.
        "controlnet": {
            "selected": (proj["pose_controlnet"] or "").strip(),
            "strength": proj["pose_cn_strength"],
            "start_percent": proj["pose_cn_start"],
            "end_percent": proj["pose_cn_end"],
            "skeleton": (proj["pose_skeleton"] or "").strip(),
        },
        "face_pass": {
            "enabled": bool(proj["pose_face_pass"]),
            "denoise": proj["pose_face_denoise"],
        },
        # Base SDXL renders a flat face at every denoise — the expression simply doesn't
        # land (docs/pose-control.md §4.0). Worth saying out loud, because it looks like a
        # broken feature rather than a wrong checkpoint.
        "checkpoint_warning": _expressive_checkpoint_warning(
            (detail.get("current_version") or {}).get("checkpoint") or ""),
    }


# Checkpoints known to render flat faces for stylised emotion. Substring match, lowercase.
_FLAT_FACE_CHECKPOINTS = ("sd_xl_base", "sd_xl_refiner", "stableDiffusionXL".lower())


def _expressive_checkpoint_warning(checkpoint: str) -> str:
    ck = (checkpoint or "").lower()
    if any(s in ck for s in _FLAT_FACE_CHECKPOINTS):
        return (f"'{checkpoint}' is a base Stable Diffusion checkpoint — measured on this "
                f"box, it renders a flat face at every face-pass denoise. Switch this "
                f"persona to an anime-capable checkpoint (e.g. NoobAI-XL) for expressive "
                f"sprites.")
    return ""


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


class PoseControlNetRequest(BaseModel):
    controlnet: str = ""                                     # registry filename, '' disables
    # 0.8.9 defaults, measured: 0.7/0.7 left the skeleton too weak to overrule a
    # strength-1.0 character LoRA, so the pose was silently ignored.
    strength: float = Field(default=1.0, ge=0.0, le=2.0)
    start_percent: float = Field(default=0.0, ge=0.0, le=1.0)
    end_percent: float = Field(default=0.9, ge=0.0, le=1.0)
    face_pass: bool = True
    face_denoise: float = Field(default=0.6, ge=0.1, le=1.0)


@app.post("/api/projects/{project_id}/pose-controlnet")
async def set_pose_controlnet(project_id: int, body: PoseControlNetRequest) -> dict:
    """Set this persona's pose-render structure + face-pass defaults (Phase H3)."""
    name = body.controlnet.strip()
    if name:
        await _check_controlnet_file(name)
    if body.end_percent < body.start_percent:
        raise HTTPException(400, "end_percent must be >= start_percent")
    warning = ""
    if body.face_denoise > 0.7:
        # 0.75 was measured destroying the face outright — allowed, but not silently.
        warning = ("face denoise above 0.70 distorts the face — 0.60 is the measured "
                   "sweet spot for driving an expression")
    with db.connect() as conn:
        cur = conn.execute(
            """UPDATE projects SET pose_controlnet = ?, pose_cn_strength = ?, pose_cn_start = ?,
                      pose_cn_end = ?, pose_face_pass = ?, pose_face_denoise = ? WHERE id = ?""",
            (name, body.strength, body.start_percent, body.end_percent,
             1 if body.face_pass else 0, body.face_denoise, project_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "project not found")
    logs.info("process", f"pose ControlNet {'set: ' + name if name else 'cleared'}",
              project_id=project_id, strength=body.strength, face_pass=body.face_pass,
              face_denoise=body.face_denoise)
    return {"controlnet": name, "strength": body.strength, "start_percent": body.start_percent,
            "end_percent": body.end_percent, "face_pass": body.face_pass,
            "face_denoise": body.face_denoise, "warning": warning}


class SkeletonRequest(BaseModel):
    # 'library' renders a stored entry at the target size — the normal path since H3b.
    # 'builtin' renders the shipped standing skeleton; 'image' takes a base64 PNG so a
    # skeleton authored elsewhere can be used without a multipart dependency.
    mode: str = "library"
    library_id: int | None = None
    image_b64: str = ""
    width: int = Field(default=832, ge=256, le=2048)
    height: int = Field(default=1216, ge=256, le=2048)
    pose_id: int | None = None          # None = set the persona default, else one pose


@app.post("/api/projects/{project_id}/pose-skeleton")
async def set_pose_skeleton(project_id: int, body: SkeletonRequest) -> dict:
    """Stage a skeleton into ComfyUI's input and bind it to the persona or one pose.

    Phase H3a's manual source. H3b replaces this with the pose library, at which point a
    binding picks a stored keypoint set and this stays as the escape hatch for a skeleton
    authored elsewhere.
    """
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    lib_id = None
    if body.mode == "library":
        if body.library_id is None:
            raise HTTPException(400, "library_id is required for mode 'library'")
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM pose_library WHERE id = ?",
                               (body.library_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "pose not found in the library")
        try:
            # Rendered fresh at the target size — this is why keypoints are stored rather
            # than a picture: one entry serves every resolution without rescaling artefacts.
            png = skeleton.render_png(json.loads(row["keypoints_json"]), body.width, body.height)
        except (json.JSONDecodeError, skeleton.SkeletonError) as exc:
            raise HTTPException(422, f"stored keypoints are not renderable: {exc}") from exc
        label, lib_id = row["name"], row["id"]
    elif body.mode == "builtin":
        png = skeleton.render_png(skeleton.STANDING_NEUTRAL, body.width, body.height)
        label = "standing-neutral"
    elif body.mode == "image":
        raw = (body.image_b64 or "").split(",")[-1]          # tolerate a data: URL prefix
        try:
            png = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(400, f"image_b64 is not valid base64: {exc}") from exc
        if not png:
            raise HTTPException(400, "image_b64 is empty")
        label = "uploaded"
    else:
        raise HTTPException(400, "mode must be 'library', 'builtin' or 'image'")

    target = f"pose_{body.pose_id}" if body.pose_id else "default"
    up = await comfy.upload_image(png, f"{slug}_{target}.png", f"pf-skeletons-{slug}")
    ref = f"{up['subfolder']}/{up['name']}" if up.get("subfolder") else up["name"]

    with db.connect() as conn:
        if body.pose_id:
            cur = conn.execute(
                "UPDATE poses SET skeleton_ref = ?, pose_library_id = ? "
                "WHERE id = ? AND project_id = ?", (ref, lib_id, body.pose_id, project_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "pose not found")
        else:
            cur = conn.execute(
                "UPDATE projects SET pose_skeleton = ?, pose_library_id = ? WHERE id = ?",
                (ref, lib_id, project_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "project not found")
    # A skeleton without a ControlNet model is inert — the render succeeds and ignores it.
    # Tell the user HERE, while they're choosing, not after a full regenerate looks unchanged.
    with db.connect() as conn:
        cn_selected = (conn.execute(
            "SELECT pose_controlnet FROM projects WHERE id = ?",
            (project_id,)).fetchone()["pose_controlnet"] or "").strip()
    warning = "" if cn_selected else (
        "no ControlNet model is selected, so this skeleton will NOT be used — renders will "
        "ignore it. Choose one under 'Pose structure & face pass' first.")

    logs.info("process", f"pose skeleton set ({label})", project_id=project_id,
              pose_id=body.pose_id, ref=ref, library_id=lib_id,
              controlnet=cn_selected or "(none selected)")
    if warning:
        logs.warn("process", f"skeleton '{label}' set but {warning}", project_id=project_id)
    return {"skeleton": ref, "source": label, "pose_id": body.pose_id, "library_id": lib_id,
            "warning": warning}


@app.post("/api/projects/{project_id}/poses/{pose_id}/face-pass")
async def pose_face_reroll(project_id: int, pose_id: int, denoise: float | None = None) -> dict:
    """Re-run pass 2 against the stored base — a new expression, same body.

    The whole point of keeping the pass-1 image: this costs seconds and cannot change the
    pose, so the expression dial can be tried repeatedly against a body already approved.
    """
    if denoise is not None and not 0.1 <= denoise <= 1.0:
        raise HTTPException(400, "denoise must be between 0.1 and 1.0")
    detail = await get_project(project_id)
    slug = detail["project"]["slug"]
    version = detail["current_version"]
    if not version:
        raise HTTPException(400, "this persona has no prompt version yet")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM poses WHERE id = ? AND project_id = ?",
                           (pose_id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, "pose not found")
        if row["status"] in ("pending", "facepass"):
            raise HTTPException(409, "this pose is still rendering")
        if not row["base_filename"]:
            raise HTTPException(400, "no base render to work from — generate this pose first")
        face_cfg = _pose_face_cfg(conn, project_id, row,
                                  _pose_library_entry(conn, project_id, row))
        if denoise is not None:
            face_cfg = {"denoise": denoise}
        elif face_cfg is None:
            # Explicitly asking for a face pass overrides the pose being switched off.
            proj = conn.execute("SELECT pose_face_denoise FROM projects WHERE id = ?",
                                (project_id,)).fetchone()
            face_cfg = {"denoise": proj["pose_face_denoise"]}
        lora_cfg = _pose_lora_cfg(conn, project_id, slug)
        try:
            started = await _queue_face_pass(conn, project_id, slug, version, row,
                                             lora_cfg, face_cfg)
        except (workflows.WorkflowError, comfy.ComfyError) as exc:
            raise HTTPException(502, f"could not queue the face pass: {exc}") from exc
    if not started:
        raise HTTPException(502, "the stored base render could not be read off the builds share")
    return {"queued": True, "pose_id": pose_id, "denoise": face_cfg["denoise"]}


# --------------------------------------------------------------------------- #
# export to SillyTavern (Phase D, 0.6.1): matte each rendered pose to a
# transparent PNG (BEN2) and save it under its SillyTavern filename. Staged into
# the build folder only — never auto-copied into ST (deliberate manual step).
# --------------------------------------------------------------------------- #

_SPRITE_RE = re.compile(r"[^a-z0-9]+")
_FOLDER_RE = re.compile(r"[^A-Za-z0-9 _-]+")


def _sprite_stem(pose_name: str, labels: set[str] | None = None) -> str:
    """The filename stem a pose exports under. A label from the emotion map (e.g. 'joy',
    or a custom tier like 'fury') is kept verbatim so SillyTavern can match it; anything
    else is slugified.

    Pass `labels` when exporting a whole set — otherwise this reads the map per pose.
    """
    low = pose_name.strip().lower()
    if low in (labels if labels is not None else set(expression_labels())):
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
    sprite_labels = set(expression_labels())   # read the map once for the whole batch
    for p in poses:
        stem = _sprite_stem(p["name"], sprite_labels)
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
