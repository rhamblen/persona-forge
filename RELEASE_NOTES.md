# v0.8.13 — Persona Forge grew a tool surface

An MCP endpoint is now served from the **same process** as the web app, at `/mcp`. Always
on, no flag, no second container. Everything the UI does, an agent can reach — through the
same code paths.

## Why a facade, not the API

The ~80 HTTP endpoints are shaped for the frontend: one per widget. Handing an agent all of
them also hands it every knob this project spent months measuring, and it will get them
wrong — ControlNet at 0.7/0.7 instead of 1.0/0.9, a face pass at denoise 0.45 that does
nothing, an expression word left in the character prompt that then leaks a smile into
`grief`.

So `backend/app/mcp_server.py` is **19 tools named for intentions**, each carrying its
invariant in the docstring where the model reads it before choosing. The rule that shapes
the list:

> **A measured number is not an argument.**

Every fact this project paid for in GPU hours lives behind a tool or in a docstring — never
in a parameter an agent has to guess. Tools call the app's own endpoints in-process over
`httpx.ASGITransport`, so a tool and the UI button beside it run the identical code path and
cannot drift.

This does **not** change the settled decision that the backend talks to ComfyUI over its
native HTTP API rather than MCP. MCP is for the agent; HTTP is for the app.

## Scope — read + queue, deliberately

| Can | Cannot |
|---|---|
| Inspect anything — projects, versions, dataset, LoRA, poses, export, jobs, logs | Delete a project, purge a dataset, drop a version |
| Create a project; append a prompt version | Roll back — the append-only history is the undo |
| Queue generate / dataset / train / poses / export | Start or restart containers |
| Add poses and presets | Copy anything into SillyTavern |

The append-only version history is the safety net for everything a tool *can* write: a bad
prompt is a new row you roll back from, not a lost one. Exports stay **staged** — moving
sprites into SillyTavern remains a deliberate human step.

## The Lore Forge handoff

`persona_create_from_dossier` consumes a dossier from Lore Forge's `lore_dossier` /
`lore_cast`. The contract lives in the new `backend/app/handoff.py`, **mirrored verbatim**
in both repos — pure stdlib, so the two copies diff byte for byte, and a dossier whose
`contract_version` major does not match is refused rather than mis-parsed.

Neither app knows the other exists; the agent carries the object across. That is what lets
the two stay separate repos, images, ports and version lines instead of merging — and it
retires the planned source-ingestion phase here, which would have rebuilt Lore Forge inside
Persona Forge.

What the tool enforces so an agent cannot get it wrong:

- The character prompt is **appearance facts only**, as prose. A fact mentioning an
  expression is dropped **whole** rather than reworded — rewriting prose into tags drops
  detail and breaks garments, and a smile in the identity renders `grief` as someone crying
  and smiling at once. Dropped facts come back named, never silently lost.
- Role, motivation and speech go to `sheet_summary` for the character *card*, not the image
  prompt. A diffusion model cannot render a motivation.
- Tier sets the size of the build: primary earns the full expression set and a LoRA,
  secondary 8 expressions, filler a single sprite.
- The canon cursor travels with the object — export `as_of_chapter=10` and the project only
  knows the book to that point, with `withheld_facts` reporting how much was held back.

## Fixed — expression budgets sampled a corner of the map

Resolving a tier's labels in map order spent a secondary character's budget of eight
entirely inside the first two axes. Measured:

    Neutral, Annoyance, Anger, Nervousness, Fear, Disappointment, Sadness, Grief

A cast member who can only ever look unhappy. Resolution is now **axis-major** — one tier
from each axis before a second from any:

    Neutral, Annoyance, Nervousness, Disappointment, Amusement, Approval,
    Disapproval, Embarrassment

Same fix and the same reasoning as 0.8.12's family-major skeleton spread: a budget must
sample the space, not a corner of it. `neutral` is always first, because it is
SillyTavern's fallback for a missing sprite and a truncated budget must never be what drops
it.

## Also

- Startup moved from `@app.on_event("startup")` to a **lifespan** — the MCP session manager
  needs a task group held open around the whole run, and Starlette ignores the `on_event`
  lists once a lifespan is supplied. `_startup()` itself is unchanged.
- Every tool returns an object, never a bare array: an empty array serialises to zero MCP
  content blocks, which reads as a malfunction rather than as "there are none".
- `backend/requirements.txt` gains `mcp==1.27.2` — the official SDK rather than a
  hand-rolled JSON-RPC endpoint (the transport spec still moves), and rather than
  `fastapi-mcp`, which derives one tool per route and is precisely the thing being avoided.
- **Compose is unchanged.** New dependency, same image build, same service definition.

## Verified

Real MCP client round trip against a running server — `initialize`, `tools/list` (19),
`tools/call` with live payloads (ComfyUI 0.29.0 and Ollama both answered). The full handoff
was exercised through real code: a dossier at `as_of_chapter=10` withheld a chapter-31 fact,
dropped an expression fact from the looks prompt and named it, seeded the project with the
resolved NoobAI-XL checkpoint, added neutral-first expressions, and refused a
`contract_version: 9.0` dossier. The route resolves at exactly `/mcp` — a bare POST returns
400, the protocol rejecting the body, not a 307 redirect. Frontend and REST API unaffected
by the lifespan change.

## Upgrade

`docker compose pull && docker compose up -d`. No compose or `.env` change.
Documentation: [`docs/mcp.md`](docs/mcp.md).
