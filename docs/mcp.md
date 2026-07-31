# The MCP tool surface

Persona Forge serves an MCP endpoint from the **same process** as the web app, at `/mcp`.
It is always on — there is no flag to enable and no second container to run.

    http://192.168.1.33:8890/mcp        deployed
    http://127.0.0.1:8890/mcp           local

## Why in-process, and why a facade

The HTTP API is shaped for the frontend: ~80 endpoints, one per widget. Handing an agent
all of them also hands it every knob this project spent months measuring, and it will get
them wrong — ControlNet at 0.7/0.7 instead of 1.0/0.9, a face pass at denoise 0.45 that
does nothing, an expression word left in the character prompt that then leaks a smile into
`grief`.

So `backend/app/mcp_server.py` is a **curated facade**: 19 tools named for intentions, each
carrying its invariant in the docstring. The rule that shapes it is **a measured number is
not an argument** — every fact the project paid for in GPU hours lives behind a tool or in
a docstring the model reads before choosing, never in a parameter an agent has to guess.

Tools call the app's own endpoints in-process over `httpx.ASGITransport`, so a tool and the
UI button beside it run the identical code path and cannot drift.

This does **not** change the settled decision that the backend talks to ComfyUI over its
native HTTP API rather than MCP. MCP is for the agent; HTTP is for the app.

## Scope: read + queue

| Can | Cannot |
|---|---|
| Inspect anything — projects, versions, dataset, LoRA, poses, export, jobs, logs | Delete a project, purge a dataset, drop a version |
| Create a project; append a prompt version | Roll back (append-only history is the undo) |
| Queue generate / dataset / train / poses / export | Start or restart containers |
| Add poses and presets | Copy anything into SillyTavern |

The append-only version history is the safety net for everything an agent *can* do: a bad
prompt written by a tool is a new row you can roll back from in the UI, not a lost one.
Exports stay **staged** — moving sprites into SillyTavern is a deliberate human step and no
tool here will do it.

## Tools

**Read** — `persona_status` · `persona_projects` · `persona_project` · `persona_versions` ·
`persona_models` · `persona_emotion_map` · `persona_pose_library` · `persona_jobs` ·
`persona_logs`

**Handoff** — `persona_create_from_dossier`

**Queue** — `persona_create_project` · `persona_save_version` · `persona_generate` ·
`persona_dataset_generate` · `persona_train_lora` · `persona_add_pose` ·
`persona_poses_preset` · `persona_generate_poses` · `persona_export_sprites`

`persona_project` is the "where is this build up to" tool: it folds project, dataset, LoRA,
poses and export state into one call so the next stage can be chosen without guessing from
the pipeline order.

## The handoff from Lore Forge

`persona_create_from_dossier` consumes the object defined by `backend/app/handoff.py`,
which is **mirrored verbatim** from Lore Forge. Neither app knows the other exists; the
agent carries the object across. A dossier whose `contract_version` major does not match is
refused rather than mis-parsed.

What the tool enforces so the agent cannot get it wrong:

- The character prompt is assembled from **appearance facts only**, as prose. A fact
  mentioning an expression is dropped **whole**, never reworded — rewriting prose into tags
  drops detail and breaks garments, and a smile in the identity renders `grief` as someone
  crying and smiling at once. Dropped facts come back named in
  `seed.dropped_expression_facts`.
- Role, motivation and speech go to `sheet_summary` for the character *card*, not into the
  image prompt. A diffusion model cannot render a motivation.
- The dossier's tier sets the size of the build: primary earns the full expression set and
  a LoRA, secondary 8 expressions, filler a single sprite.

### The expression budget is resolved here, not in the contract

The contract carries a **count**, never a list of labels — the vocabulary lives in this
app's editable emotion map, so a frozen copy in the shared module would drift the moment
someone renames a tier.

Resolution is **axis-major**, and that detail matters. Taking the first N labels in map
order spent a secondary character's budget of eight entirely inside the first two axes —
measured: `Neutral, Annoyance, Anger, Nervousness, Fear, Disappointment, Sadness, Grief`, a
cast member who can only ever look unhappy. Walking one tier from each axis before taking a
second from any gives `Neutral, Annoyance, Nervousness, Disappointment, Amusement,
Approval, Disapproval, Embarrassment` instead. This is the same fix the dataset skeleton
spread already makes for posture families, for the same reason: a budget must sample the
space, not a corner of it.

`neutral` is always first. It is SillyTavern's fallback when a requested sprite is missing,
so a truncated budget must never be what drops it.

## Client configuration

The endpoint speaks streamable HTTP, stateless, with JSON responses — no session to lose
when the container restarts. House preference is the `mcp-remote` bridge rather than a
native HTTP transport:

```json
{
  "mcpServers": {
    "persona-forge": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.1.33:8890/mcp"]
    },
    "lore-forge": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.1.33:8891/mcp"]
    }
  }
}
```

Connect both. The agent holding the two is what makes the LF→PF seam work without either
service depending on the other.

## Implementation notes

- The route sits at **exactly** `/mcp`, added as a Starlette `Route` whose endpoint is an
  ASGI-app *object*. Mounting a sub-app instead would serve `/mcp/` and answer `/mcp` with
  a 307, which not every client follows. (A bare `POST /mcp` with no MCP envelope correctly
  returns 400 — that is the protocol rejecting the body, and proof the path resolves.)
- Startup moved from `@app.on_event("startup")` to a **lifespan**, because the session
  manager needs a task group held open around the whole run and Starlette ignores the
  `on_event` lists once a lifespan is supplied. `_startup()` itself is unchanged.
- Every tool returns an object, never a bare array: an empty array serialises to zero MCP
  content blocks, which reads as a malfunction rather than as "there are none".
- Boot logs `MCP tool surface mounted` with the tool count and contract version.
