"""Thin ComfyUI HTTP client.

Deliberately talks to ComfyUI's native API rather than going through MCP — MCP is a
wrapper for LLM tool-calling and would only add a JSON-RPC hop to these same
endpoints. See PROJECT_PLAN 5.2.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from . import logs

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://192.168.1.33:9000").rstrip("/")


class ComfyError(RuntimeError):
    pass


async def system_stats() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.get(f"{COMFYUI_URL}/system_stats")
        r.raise_for_status()
        return r.json()


async def object_info(node: str | None = None) -> dict[str, Any]:
    """Node schemas — used to populate model/sampler dropdowns from live state."""
    url = f"{COMFYUI_URL}/object_info" + (f"/{node}" if node else "")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


async def list_models(kind: str = "checkpoints") -> list[str]:
    """kind: 'checkpoints' | 'loras'. Reads the live dropdown values from ComfyUI."""
    node, field = {
        "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
        "loras": ("LoraLoaderModelOnly", "lora_name"),
    }.get(kind, ("CheckpointLoaderSimple", "ckpt_name"))
    info = await object_info(node)
    try:
        return list(info[node]["input"]["required"][field][0])
    except (KeyError, IndexError, TypeError):
        return []


async def submit(graph: dict[str, Any], client_id: str = "persona-forge") -> str:
    """POST an API-format workflow. Returns prompt_id."""
    logs.debug("integration", "submitting workflow to ComfyUI", nodes=len(graph), url=COMFYUI_URL)
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f"{COMFYUI_URL}/prompt", json={"prompt": graph, "client_id": client_id})
        if r.status_code >= 400:
            logs.error("integration", f"ComfyUI rejected the workflow ({r.status_code})",
                       body=r.text[:500])
            raise ComfyError(f"ComfyUI rejected the workflow ({r.status_code}): {r.text[:800]}")
        data = r.json()
    if data.get("node_errors"):
        logs.error("integration", "ComfyUI reported node_errors", node_errors=data["node_errors"])
        raise ComfyError(f"node_errors: {data['node_errors']}")
    logs.info("integration", "workflow queued", prompt_id=data["prompt_id"])
    return data["prompt_id"]


async def wait(prompt_id: str, timeout_s: float = 900.0, poll_s: float = 2.0) -> dict[str, Any]:
    """Poll /history until the prompt finishes. Returns the history entry."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=15.0) as c:
        while asyncio.get_event_loop().time() < deadline:
            r = await c.get(f"{COMFYUI_URL}/history/{prompt_id}")
            if r.status_code == 200:
                hist = r.json()
                if prompt_id in hist:
                    entry = hist[prompt_id]
                    status = (entry.get("status") or {}).get("status_str")
                    if status in ("success", "error"):
                        lvl = logs.info if status == "success" else logs.error
                        lvl("integration", f"prompt {status}", prompt_id=prompt_id,
                            waited_s=round(asyncio.get_event_loop().time() - (deadline - timeout_s)))
                        return entry
            await asyncio.sleep(poll_s)
    logs.error("integration", "timed out waiting for prompt", prompt_id=prompt_id, timeout_s=timeout_s)
    raise ComfyError(f"timed out waiting for prompt {prompt_id}")


def outputs_from(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten produced images out of a history entry."""
    out: list[dict[str, str]] = []
    for node_out in (entry.get("outputs") or {}).values():
        for img in node_out.get("images", []) or []:
            out.append(
                {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
            )
    return out


def view_url(filename: str, subfolder: str = "", type_: str = "output") -> str:
    from urllib.parse import urlencode

    q = urlencode({"filename": filename, "subfolder": subfolder, "type": type_})
    return f"{COMFYUI_URL}/view?{q}"


def error_message(entry: dict[str, Any]) -> str | None:
    for msg in (entry.get("status") or {}).get("messages", []) or []:
        if msg and msg[0] == "execution_error":
            info = msg[1]
            return f"{info.get('node_type')}: {info.get('exception_message')}"
    return None
