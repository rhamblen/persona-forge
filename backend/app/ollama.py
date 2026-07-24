"""Thin Ollama client for natural-language prompt authoring.

One bounded job (PROJECT_PLAN §3): given a plain-language description plus the
current prompt, return revised text for the three prompt fields — character,
style, negative. Runs on the LAN Ollama box; no Claude in the runtime loop.

Design constraints carried from the project's settled decisions:
  * PROSE, not Danbooru tags — the user writes prose and tag rewrites drop detail.
  * Expression words must NOT sit in the character field — a baked-in smile leaks
    into anger/grief. The model is told to keep identity and expression separate.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from . import logs

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.32:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
# How long Ollama keeps the model in VRAM after a request. Because the Ollama box
# is a shared, always-on container, we don't want a persona prompt to pin VRAM
# forever — this makes it auto-unload after a spell of no use. The sidebar's
# Connect/Unload buttons override it on demand.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")


class OllamaError(RuntimeError):
    pass


_SYSTEM = """You are a prompt-authoring assistant for an anime image generator (SDXL/NoobAI).
You maintain THREE separate prompt fields for one character:

  character  — the character's fixed identity ONLY: body, face shape, hair, eye
               colour, skin, permanent features, default outfit. Written as prose,
               not comma-separated tags. NEVER put a facial expression, emotion,
               mood, pose or camera framing in this field — those change per shot
               and would contaminate every expression.
  style      — art style, rendering, lighting, background and camera framing. Prose.
  negative   — things to avoid (low quality, extra fingers, watermark, etc.).

Rules:
  - Write natural prose, NOT Danbooru/booru tag lists.
  - Keep expression/emotion/pose words OUT of `character`; they belong nowhere here
    unless the user explicitly asks for a default pose in `style`.
  - Preserve concrete details the user already wrote; do not silently drop them.
  - Reply with ONLY a JSON object, no prose around it, exactly:
    {"character": "...", "style": "...", "negative": "..."}
"""

_MODE_HINT = {
    "replace": ("Write all three fields FRESH from the user's description. "
                "Ignore the current values below except as loose context."),
    "modify": ("EDIT the current values to satisfy the user's instruction. "
               "Keep everything the instruction does not touch. Return the full "
               "updated text for all three fields, not just the changes."),
}


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply, tolerating stray prose."""
    text = text.strip()
    # strip a ```json ... ``` fence if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # last resort: first balanced-looking {...} span
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise OllamaError("model did not return JSON")


async def status() -> dict[str, Any]:
    """Reachability, available models, and whether our model is loaded in VRAM.

    `loaded` drives the sidebar: reachable-but-not-loaded shows Connect; loaded
    shows Unload and means the first Suggest will be instant.
    """
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            loaded_names: list[str] = []
            try:
                ps = await c.get(f"{OLLAMA_URL}/api/ps")
                if ps.status_code == 200:
                    loaded_names = [m.get("name", "") for m in ps.json().get("models", [])]
            except httpx.HTTPError:
                pass  # /api/ps is best-effort; reachability already established
        return {
            "reachable": True, "url": OLLAMA_URL, "model": OLLAMA_MODEL,
            "models": models, "loaded": OLLAMA_MODEL in loaded_names,
            "loaded_models": loaded_names,
        }
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "url": OLLAMA_URL, "model": OLLAMA_MODEL,
                "loaded": False, "error": str(exc)}


async def _set_keep_alive(keep_alive: Any, model: str | None = None) -> dict[str, Any]:
    """Load (keep_alive > 0) or unload (keep_alive == 0) a model without generating."""
    model = model or OLLAMA_MODEL
    payload = {"model": model, "keep_alive": keep_alive}  # no "prompt" → load/unload only
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as c:
        r = await c.post(f"{OLLAMA_URL}/api/generate", json=payload)
        r.raise_for_status()
    return {"model": model}


async def warm(model: str | None = None) -> dict[str, Any]:
    """Preload the model so the next Suggest skips the ~60s cold load."""
    model = model or OLLAMA_MODEL
    logs.info("integration", "warming Ollama model", model=model, keep_alive=OLLAMA_KEEP_ALIVE)
    try:
        await _set_keep_alive(OLLAMA_KEEP_ALIVE, model)
    except httpx.HTTPError as exc:
        logs.error("integration", f"could not warm Ollama: {exc}", model=model)
        raise OllamaError(f"could not warm Ollama: {exc}") from exc
    return {"warmed": True, "model": model, "keep_alive": OLLAMA_KEEP_ALIVE}


async def unload(model: str | None = None) -> dict[str, Any]:
    """Free the model from VRAM immediately (keep_alive=0)."""
    model = model or OLLAMA_MODEL
    logs.info("integration", "unloading Ollama model", model=model)
    try:
        await _set_keep_alive(0, model)
    except httpx.HTTPError as exc:
        logs.error("integration", f"could not unload Ollama: {exc}", model=model)
        raise OllamaError(f"could not unload Ollama: {exc}") from exc
    return {"unloaded": True, "model": model}


async def suggest_prompt(instruction: str, mode: str, current: dict[str, str],
                         model: str | None = None) -> dict[str, str]:
    """Return {character, style, negative} suggested by Ollama.

    mode: 'replace' (author fresh) or 'modify' (edit the current values).
    """
    if mode not in _MODE_HINT:
        raise OllamaError(f"unknown mode: {mode!r}")
    model = model or OLLAMA_MODEL

    user = (
        f"{_MODE_HINT[mode]}\n\n"
        f"User instruction:\n{instruction.strip()}\n\n"
        "Current values:\n"
        f"  character: {current.get('character', '') or '(empty)'}\n"
        f"  style: {current.get('style', '') or '(empty)'}\n"
        f"  negative: {current.get('negative', '') or '(empty)'}\n"
    )
    payload = {
        "model": model,
        "system": _SYSTEM,
        "prompt": user,
        "stream": False,
        "format": "json",
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.7},
    }

    logs.info("integration", "requesting prompt suggestion from Ollama",
              model=model, mode=mode, url=OLLAMA_URL)
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            reply = r.json().get("response", "")
    except httpx.HTTPError as exc:
        logs.error("integration", f"Ollama request failed: {exc}", url=OLLAMA_URL)
        raise OllamaError(f"Ollama request failed: {exc}") from exc

    data = _extract_json(reply)
    out = {
        "character": str(data.get("character", "")).strip(),
        "style": str(data.get("style", "")).strip(),
        "negative": str(data.get("negative", "")).strip(),
    }
    # Modify must never destroy a field the instruction didn't touch: small models
    # sometimes return "" for a field they simply left alone. Keep the current value.
    if mode == "modify":
        for k in out:
            if not out[k] and current.get(k):
                out[k] = current[k]
    logs.info("integration", "Ollama returned a suggestion",
              chars={k: len(v) for k, v in out.items()})
    return out
