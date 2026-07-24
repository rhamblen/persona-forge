"""Start/restart the ComfyUI and Ollama containers, via a scoped socket proxy.

Persona Forge runs on the SAME host (UR1) as the ComfyUI (`stable-diffusion-ComfyUI`)
and Ollama (`ollama`, a br0 macvlan container at .32) services, so it can drive them
through the Docker Engine API. It NEVER touches the raw docker socket — it talks to a
`tecnativa/docker-socket-proxy` sidecar scoped to list/inspect + start/restart only
(see docker-compose.yml). If DOCKER_PROXY_URL is unset the feature is disabled.

This only recovers a *stopped container on a live host*. If UR1 itself is down, so is
Persona Forge — nothing here can help with that.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from . import logs

DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "").rstrip("/")

# Logical key -> real container name. Keys are what the API/UI use.
CONTAINERS = {
    "comfyui": os.getenv("COMFYUI_CONTAINER", "stable-diffusion-ComfyUI"),
    "ollama": os.getenv("OLLAMA_CONTAINER", "ollama"),
}


class DockerCtlError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(DOCKER_PROXY_URL)


def resolve(key: str) -> str:
    name = CONTAINERS.get(key)
    if not name:
        raise DockerCtlError(f"unknown container key: {key!r}")
    return name


def _require_enabled() -> None:
    if not enabled():
        raise DockerCtlError("container control is disabled (DOCKER_PROXY_URL not set)")


async def _inspect(name: str) -> dict[str, Any] | None:
    """Return the container's State dict, or None if it does not exist."""
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.get(f"{DOCKER_PROXY_URL}/containers/{name}/json")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("State") or {}


async def state(key: str) -> dict[str, Any]:
    """{'exists', 'running', 'status'} for one container. Best-effort; never raises
    for connectivity so the sidebar can render a degraded state."""
    if not enabled():
        return {"enabled": False, "exists": None, "running": None, "status": "disabled"}
    name = resolve(key)
    try:
        st = await _inspect(name)
    except httpx.HTTPError as exc:
        logs.warn("integration", f"docker proxy unreachable: {exc}", container=name)
        return {"enabled": True, "exists": None, "running": None, "status": "proxy-unreachable"}
    if st is None:
        return {"enabled": True, "exists": False, "running": False, "status": "not-found"}
    return {
        "enabled": True,
        "exists": True,
        "running": bool(st.get("Running")),
        "status": st.get("Status", ""),
    }


async def _post(name: str, action: str) -> None:
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f"{DOCKER_PROXY_URL}/containers/{name}/{action}")
        # 204 = done, 304 = already in that state (e.g. start an already-running one)
        if r.status_code not in (204, 304):
            raise DockerCtlError(f"{action} failed ({r.status_code}): {r.text[:200]}")


async def start(key: str) -> dict[str, Any]:
    _require_enabled()
    name = resolve(key)
    logs.info("integration", "starting container", container=name, key=key)
    try:
        await _post(name, "start")
    except httpx.HTTPError as exc:
        raise DockerCtlError(f"could not start {name}: {exc}") from exc
    return {"container": name, "action": "start"}


async def restart(key: str) -> dict[str, Any]:
    _require_enabled()
    name = resolve(key)
    logs.info("integration", "restarting container", container=name, key=key)
    try:
        await _post(name, "restart")
    except httpx.HTTPError as exc:
        raise DockerCtlError(f"could not restart {name}: {exc}") from exc
    return {"container": name, "action": "restart"}
