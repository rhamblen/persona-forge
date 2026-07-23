"""Persona Forge — 0.1.x skeleton.

Purpose of this milestone: prove the deploy loop and verify the two pieces of
infrastructure everything else depends on —
  1. we can reach ComfyUI, and
  2. the shared builds folder is genuinely readable AND writable from this
     container (the same folder ComfyUI writes to).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://192.168.1.33:9000").rstrip("/")
BUILDS_ROOT = Path(os.getenv("BUILDS_ROOT", "/builds"))
APPDATA_ROOT = Path(os.getenv("APPDATA_ROOT", "/appdata"))
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_version_file = Path(__file__).resolve().parent.parent / "VERSION"
VERSION = _version_file.read_text().strip() if _version_file.exists() else "0.0.0"

app = FastAPI(title="Persona Forge", version=VERSION)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/api/comfyui/status")
async def comfyui_status() -> dict:
    """Live ComfyUI connection check — drives the sidebar indicator."""
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{COMFYUI_URL}/system_stats")
            r.raise_for_status()
            stats = r.json()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        return {
            "connected": False,
            "url": COMFYUI_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }

    latency_ms = round((time.perf_counter() - started) * 1000)
    system = stats.get("system", {}) or {}
    devices = stats.get("devices", []) or []
    gpu = devices[0] if devices else {}

    return {
        "connected": True,
        "url": COMFYUI_URL,
        "latency_ms": latency_ms,
        "comfyui_version": system.get("comfyui_version"),
        "python_version": (system.get("python_version") or "").split()[0] or None,
        "gpu": gpu.get("name"),
        "vram_total_mb": round(gpu["vram_total"] / 1048576) if gpu.get("vram_total") else None,
        "vram_free_mb": round(gpu["vram_free"] / 1048576) if gpu.get("vram_free") else None,
    }


def _probe_writable(path: Path) -> tuple[bool, str | None]:
    """Actually write a temp file — permissions on these shares are the known risk."""
    probe = path / f".pf_write_probe_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("ok")
        probe.unlink()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


@app.get("/api/storage/status")
async def storage_status() -> dict:
    """Verify the shared builds mount — this is the constraint from PROJECT_PLAN 5.1."""
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


@app.get("/api/builds")
async def list_builds() -> dict:
    """List existing build folders (each is a named project)."""
    if not BUILDS_ROOT.is_dir():
        return {"builds": [], "error": "builds root not mounted"}
    builds = []
    for entry in sorted(p for p in BUILDS_ROOT.iterdir() if p.is_dir()):
        builds.append(
            {
                "name": entry.name,
                "has_lora": (entry / "lora").is_dir(),
                "has_images": (entry / "images").is_dir(),
                "image_count": len(list((entry / "images").glob("*.png"))) if (entry / "images").is_dir() else 0,
            }
        )
    return {"builds": builds}


# --- static frontend -------------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")
