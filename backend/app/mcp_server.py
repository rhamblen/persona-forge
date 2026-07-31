"""Persona Forge's MCP tool surface — in-process, mounted at `/mcp`.

Why this exists, and why it is *not* a separate service:

The HTTP API is shaped for the frontend — ~80 endpoints, one per widget. Handing an agent
all of them would also hand it every knob this project spent months measuring, and it
would get them wrong: ControlNet at 0.7/0.7 instead of 1.0/0.9, a face pass at denoise
0.45 that does nothing, an expression word left in the character prompt that then leaks a
smile into `grief`. So this module is a **curated facade**, not a route-to-tool dump.

The rule that shapes it: **a measured number is not an argument.** Every fact the project
paid for in GPU hours lives behind a tool, in the endpoint the UI already calls, or in a
docstring the model reads before choosing — never in a parameter an agent has to guess.

**Scope is read + queue** (decided 2026-07-31). An agent can inspect anything and start
any job. It cannot delete a project, purge a dataset, drop a prompt version, roll back, or
start/restart containers — those stay in the UI. The append-only version history is the
safety net for everything an agent *can* do here: a bad prompt written by a tool is a new
row you can roll back from, not a lost one.

Implementation note: tools call the app's own endpoints in-process over
`httpx.ASGITransport`, so a tool and the UI button beside it run the identical code path
and cannot drift.
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.routing import Route

from . import handoff

INSTRUCTIONS = """\
Persona Forge turns a character concept into a trained LoRA and a set of SillyTavern
expression sprites.

The pipeline is ordered and each stage feeds the next: prompt → dataset → LoRA → poses →
export. `persona_project` tells you where a project actually is.

The durable asset is the **LoRA**, not the sprites. It is what keeps a posture change the
same person, and it can be re-used later for things sprites cannot do — so a project with
a trained LoRA and no sprites has kept the valuable half.

Prompt discipline, proven on this pipeline and not up for renegotiation:
- **Prose, not Danbooru tags.** Rewriting prose into tags drops detail and breaks
  garments. Only mechanical edits are allowed.
- **No expression words in the character prompt.** A smile baked into the identity gives
  you a sprite that is crying and smiling at once.

Exports are **staged, never copied into SillyTavern**. Moving them is a deliberate human
step, and the tools here will not do it.

`persona_create_from_dossier` accepts a dossier from Lore Forge's `lore_dossier` /
`lore_cast`. The two apps share `handoff.py` and nothing else.
"""

mcp: FastMCP = FastMCP(
    name="persona-forge",
    instructions=INSTRUCTIONS,
    stateless_http=True,   # no session state to lose when the container restarts
    json_response=True,    # plain JSON responses; no SSE stream to hold open
)

_APP: Any = None
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        if _APP is None:  # pragma: no cover - install() runs at import time
            raise RuntimeError("mcp_server.install(app) has not run")
        _client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_APP, raise_app_exceptions=False),
            base_url="http://persona-forge.internal",
            timeout=httpx.Timeout(900.0),
        )
    return _client


async def _api(method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
    """One in-process call. A 4xx/5xx becomes an exception carrying the API's own message
    — those messages name the missing prerequisite, which is what the agent needs."""
    r = await _http().request(method, path, json=json, params=params)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:  # noqa: BLE001
            detail = r.text
        raise RuntimeError(f"{path} → {r.status_code}: {detail}")
    return r.json()


def _obj(payload: Any, key: str) -> dict:
    """Wrap a bare JSON array in an object — an empty list serialises to zero MCP content
    blocks, which reads as a malfunction rather than as "there are none"."""
    return payload if isinstance(payload, dict) else {key: payload, "count": len(payload or [])}


# --------------------------------------------------------------------------- #
# read — where things stand
# --------------------------------------------------------------------------- #

@mcp.tool()
async def persona_status() -> dict:
    """Version, ComfyUI, Ollama, the shared builds mount and container state.

    Call this first. ComfyUI unreachable or the builds mount not writable are the two
    failures that make every generate tool below fail in a confusing way — the mount is
    write-probed rather than assumed, because ComfyUI writes into it as a different user
    from a different container."""
    out: dict[str, Any] = {"contract_version": handoff.CONTRACT_VERSION}
    for key, path in (("health", "/api/health"), ("comfyui", "/api/comfyui/status"),
                      ("storage", "/api/storage/status"), ("ollama", "/api/ai/status"),
                      ("containers", "/api/containers/status")):
        try:
            out[key] = await _api("GET", path)
        except RuntimeError as exc:
            out[key] = {"error": str(exc)}
    return out


@mcp.tool()
async def persona_projects() -> dict:
    """Every persona project. A project is a named build folder plus its prompt history."""
    return await _api("GET", "/api/projects")


@mcp.tool()
async def persona_project(project_id: int) -> dict:
    """One project end to end: current prompt, dataset progress, LoRA state, poses, export.

    This is the "where is this build up to" tool — it answers which stage to run next
    without guessing from the pipeline order."""
    out: dict[str, Any] = {"project": await _api("GET", f"/api/projects/{project_id}")}
    for key, path in (("dataset", f"/api/projects/{project_id}/dataset"),
                      ("lora", f"/api/projects/{project_id}/lora"),
                      ("poses", f"/api/projects/{project_id}/poses"),
                      ("export", f"/api/projects/{project_id}/poses/export")):
        try:
            out[key] = await _api("GET", path)
        except RuntimeError as exc:
            out[key] = {"error": str(exc)}
    return out


@mcp.tool()
async def persona_versions(project_id: int) -> dict:
    """The append-only prompt history, with sign-off state.

    Nothing here is ever deleted or mutated — an edit appends a child version and moves
    `current`. That is what makes an agent-written prompt safe: it is a new row you can
    roll back from in the UI."""
    return await _api("GET", f"/api/projects/{project_id}/versions")


@mcp.tool()
async def persona_models(kind: str = "checkpoints") -> dict:
    """What ComfyUI can load: `checkpoints`, `loras`, or `controlnet`.

    A trained LoRA lands in the project's build folder, which ComfyUI only sees if
    `extra_model_paths.yaml` maps `loras → /builds`. If a LoRA you just trained is missing
    from this list, that mapping is why."""
    return await _api("GET", "/api/models", params={"kind": kind})


@mcp.tool()
async def persona_emotion_map() -> dict:
    """The expression vocabulary — axes, tiers and their prompt modifiers.

    This is the source of truth for sprite labels; it is user-editable, so read it rather
    than assuming the SillyTavern 28. `custom` tiers are ones ST's own classifier cannot
    emit — they need a manual trigger to ever appear."""
    return await _api("GET", "/api/emotion-map")


@mcp.tool()
async def persona_pose_library() -> dict:
    """The skeleton catalogue used for structural pose control, by posture family."""
    return await _api("GET", "/api/pose-library")


@mcp.tool()
async def persona_jobs(job_id: int | None = None) -> dict:
    """Job state. Everything queued below is polled here; the worker advances jobs
    unattended, independent of any open browser."""
    if job_id is not None:
        return await _api("GET", f"/api/jobs/{job_id}")
    return await _api("GET", "/api/jobs")


@mcp.tool()
async def persona_logs(level: str = "", category: str = "", search: str = "",
                       limit: int = 100) -> dict:
    """Recent log lines. Categories: boot, integration, process, local, api."""
    params: dict[str, Any] = {"limit": limit}
    for key, value in (("level", level), ("category", category), ("search", search)):
        if value:
            params[key] = value
    return await _api("GET", "/api/logs", params=params)


# --------------------------------------------------------------------------- #
# the handoff — consuming a Lore Forge dossier
# --------------------------------------------------------------------------- #

async def _expressions_for(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Resolve a tier's expression labels from *this* app's emotion map.

    The contract carries a count, never a list of labels: the vocabulary is editable here,
    so a frozen copy in the shared module would drift the moment someone renames a tier.
    Built-ins only — a custom tier cannot be emitted by SillyTavern's classifier, so
    filling a thin character's budget with custom labels would buy sprites that never show.

    **Axis-major, not map order.** Taking the first N in axis/tier order spends a
    secondary character's whole budget of eight inside the first two axes — measured: it
    returned Neutral, Annoyance, Anger, Nervousness, Fear, Disappointment, Sadness, Grief,
    a cast member who can only ever look unhappy. Walking one tier from each axis before
    taking a second from any is the same fix the dataset skeleton spread already makes for
    posture families, for the same reason: a budget must sample the space, not a corner
    of it. Tier order within an axis is kept, so a truncated set gets the mild expressions
    rather than the extreme ones.
    """
    emap = await _api("GET", "/api/emotion-map")
    fallback = handoff.FALLBACK_EXPRESSION.lower()

    by_axis: list[list[dict[str, str]]] = []
    neutral: list[dict[str, str]] = []
    for axis in emap.get("axes", []):
        column = []
        for tier in axis.get("tiers", []):
            if tier.get("custom"):
                continue
            row = {"name": str(tier["label"]).capitalize(),
                   "modifier": tier.get("modifier", "")}
            # `neutral` is pulled out and placed first: it is SillyTavern's fallback when
            # a sprite is missing, so a truncated budget must never be what drops it.
            (neutral if row["name"].lower() == fallback else column).append(row)
        if column:
            by_axis.append(column)

    ordered = list(neutral)
    for depth in range(max((len(c) for c in by_axis), default=0)):
        for column in by_axis:
            if depth < len(column):
                ordered.append(column[depth])

    budget = plan.get("expressions")
    return ordered if budget is None else ordered[:max(1, int(budget))]


@mcp.tool()
async def persona_create_from_dossier(dossier: dict, name: str = "",
                                      add_expressions: bool = True) -> dict:
    """Open a project from a Lore Forge dossier — the LF→PF handoff.

    Takes the `dossier` object from `lore_dossier` or one entry of `lore_cast`. The
    character prompt is assembled from the dossier's *appearance* facts as prose, with any
    fact mentioning an expression dropped whole rather than reworded — both rules are
    proven here and the tool enforces them so the agent cannot.

    Role, motivation and speech are deliberately **not** put in the image prompt: a
    diffusion model cannot render a motivation. They come back in `sheet_summary` for the
    character card.

    The dossier's tier decides the size of the build (`plan`). With `add_expressions`,
    that many expression poses are created up front, `neutral` first — it is
    SillyTavern's fallback and must never be the one a truncated budget drops.

    Spoiler control travels with the object: if the dossier was exported `as_of_chapter`,
    this project only knows the book up to that point, and `withheld_facts` says how much
    was held back.
    """
    problems = handoff.validate(dossier)
    if problems:
        raise ValueError(f"unusable dossier: {'; '.join(problems)}")

    seed = handoff.persona_seed(dossier)
    if not seed["character"]:
        raise ValueError(
            f"{seed['name']}'s dossier has no usable appearance facts — nothing to render. "
            "Run Lore Forge's sheets pass for this character, or check whether every "
            "appearance fact was dropped as an expression.")

    created = await _api("POST", "/api/projects", json={
        "name": name or seed["name"],
        "character": seed["character"],
        "style": "",
    })
    project_id = int(created["project"]["id"])

    added: list[str] = []
    if add_expressions:
        for row in await _expressions_for(seed["plan"]):
            with contextlib.suppress(RuntimeError):
                await _api("POST", f"/api/projects/{project_id}/poses", json=row)
                added.append(row["name"])

    return {
        "project_id": project_id,
        "project": created,
        "seed": seed,
        "expressions_added": added,
        "next": ("Review the character prompt, then persona_generate for a preview. "
                 "Dataset → LoRA → poses → export from there."),
    }


# --------------------------------------------------------------------------- #
# queue — start work, then poll
# --------------------------------------------------------------------------- #

@mcp.tool()
async def persona_create_project(name: str, character: str, style: str = "",
                                 negative: str = "", checkpoint: str = "",
                                 seed: int = 123456789) -> dict:
    """Create a project from a written prompt. Prefer `persona_create_from_dossier` when
    the character came from a book.

    `character` is prose describing how they look, and must contain no expression words.
    Leave `checkpoint` empty to get the resolved anime-first default rather than whatever
    ComfyUI happens to list first, and `negative` empty to get the canonical starter
    negative — a persona with no negatives renders low-quality junk."""
    return await _api("POST", "/api/projects", json={
        "name": name, "character": character, "style": style,
        "negative": negative, "checkpoint": checkpoint, "seed": seed})


@mcp.tool()
async def persona_save_version(project_id: int, character: str | None = None,
                               style: str | None = None, negative: str | None = None,
                               checkpoint: str | None = None, seed: int | None = None,
                               note: str = "") -> dict:
    """Append a new prompt version and make it current. Nothing is overwritten.

    Only the fields you pass change; the rest carry forward. Write a `note` — the history
    is rendered as a diff rail and an unexplained change is the one that gets rolled back
    blind. Keep expression words out of `character`."""
    body = {"source": "manual", "note": note or "via MCP"}
    for key, value in (("character", character), ("style", style), ("negative", negative),
                       ("checkpoint", checkpoint), ("seed", seed)):
        if value is not None:
            body[key] = value
    return await _api("POST", f"/api/projects/{project_id}/versions", json=body)


@mcp.tool()
async def persona_generate(project_id: int, workflow: str = "base-character") -> dict:
    """Render one preview image from the project's current prompt.

    The cheap check that a prompt is working before spending a 30-image dataset and a
    training run on it."""
    return await _api("POST", f"/api/projects/{project_id}/generate",
                      json={"workflow": workflow, "params": {}, "wait": True})


@mcp.tool()
async def persona_dataset_generate(project_id: int, count: int = 30,
                                   mode: str = "both") -> dict:
    """Queue `count` training candidates at fresh seeds, then cherry-pick in the UI.

    Variety is the point: candidates spread across framing and expression, because a
    same-framing batch trains a pose-locked LoRA. `mode` — "both", "faces" (close-ups, to
    strengthen a weak face) or "poses" (full body, to strengthen weak posture).

    Selection stays human. Deciding which images are the same person is exactly the
    judgement the LoRA's quality rests on."""
    if mode not in ("both", "faces", "poses"):
        raise ValueError("mode must be one of: both, faces, poses")
    return await _api("POST", f"/api/projects/{project_id}/dataset/generate",
                      json={"count": count, "mode": mode, "pose_variety": True})


@mcp.tool()
async def persona_train_lora(project_id: int, steps: int = 500, rank: int = 16,
                             learning_rate: float = 0.0005) -> dict:
    """Train the character LoRA from the selected dataset images.

    This is the durable asset — everything downstream is regenerable from it. VRAM is
    freed first (Ollama unloaded, ComfyUI `/free`) to avoid an OOM, so expect the AI
    assistant to go cold while this runs. The defaults are the validated ones; change them
    only with a reason."""
    return await _api("POST", f"/api/projects/{project_id}/lora/train", json={
        "steps": steps, "rank": rank, "learning_rate": learning_rate})


@mcp.tool()
async def persona_add_pose(project_id: int, name: str, modifier: str = "") -> dict:
    """Add one expression or pose to a project. `name` becomes the sprite filename.

    Use this for a filler-tier character, who earns exactly one sprite — and that one must
    be `neutral`, SillyTavern's fallback when a requested expression is missing."""
    return await _api("POST", f"/api/projects/{project_id}/poses",
                      json={"name": name, "modifier": modifier})


@mcp.tool()
async def persona_poses_preset(project_id: int, preset: str = "starter") -> dict:
    """Add a shipped preset's poses. `starter` is eight camera framings; `expressions` is
    the SillyTavern built-in set; `expressions-tiered` adds the custom tiers on top.

    Existing poses are never duplicated. For a reduced per-tier set, use
    `persona_create_from_dossier` (which resolves the budget from the emotion map) or add
    them individually."""
    return await _api("POST", f"/api/projects/{project_id}/poses/preset",
                      json={"preset": preset})


@mcp.tool()
async def persona_generate_poses(project_id: int) -> dict:
    """Render every pending pose for this project.

    Two passes: body under a ControlNet skeleton, then the face repainted over the stored
    body image. That split is why a face re-roll is seconds rather than two minutes and
    cannot disturb an approved body. The measured settings are applied for you."""
    return await _api("POST", f"/api/projects/{project_id}/poses/generate-all")


@mcp.tool()
async def persona_export_sprites(project_id: int) -> dict:
    """Matte every rendered pose to a transparent, SillyTavern-named PNG.

    Background removal is BEN2 — the only one that works in this environment. Output is
    **staged** in the project's `export/` folder and is never copied into SillyTavern;
    moving it there stays a deliberate human step."""
    return await _api("POST", f"/api/projects/{project_id}/poses/export")


# --------------------------------------------------------------------------- #
# mounting
# --------------------------------------------------------------------------- #

class _ASGIEndpoint:
    """A class, not a function, on purpose: Starlette treats a plain function endpoint as
    a request/response handler and only an object as a raw ASGI app. Wrapping the session
    manager this way lets the route sit at exactly `/mcp` — mounting a sub-app instead
    would serve `/mcp/` and answer `/mcp` with a 307, which not every client follows."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._manager.handle_request(scope, receive, send)


def install(app: Any, path: str = "/mcp") -> None:
    """Add the MCP endpoint to an existing FastAPI app. Call once, at import time."""
    global _APP
    _APP = app
    mcp.streamable_http_app()          # lazily builds the session manager
    app.router.routes.append(
        Route(path, endpoint=_ASGIEndpoint(mcp.session_manager)))


@contextlib.asynccontextmanager
async def session() -> AsyncIterator[None]:
    """Run the MCP session manager for the life of the app. Enter this from the app's
    lifespan — without it the endpoint accepts requests and then hangs."""
    async with mcp.session_manager.run():
        yield


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
