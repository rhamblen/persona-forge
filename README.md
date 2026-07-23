# Persona Forge

Repo: https://github.com/rhamblen/persona-forge

## Why

Sometimes when using SillyTavern you want to create your own characters with your
own images — but you don't want a static image, you want expressions. Live2D and VRM
are complicated to build, and facial expressions alone are easy to misread or simply
not notice, especially across 28 of them. Using **posture** as well makes the
character far more readable.

Persona Forge builds both **face and posture** expression sets using ComfyUI, behind
a custom interface that walks you through the steps — with **Ollama** on hand to
customise prompts in natural language wherever you need it.

## What it does

Self-hosted web app for building consistent 2D anime characters end to end:

**Prompt Studio → Dataset Builder → per-character LoRA → Pose / Expression sets**
→ export to SillyTavern.

- Natural-language prompt editing (via a local Ollama model)
- Full prompt **version history + rollback** (never lose a signed-off prompt)
- Drives the existing ComfyUI on UR1; runs entirely on the LAN

See **[PROJECT_PLAN.md](PROJECT_PLAN.md)** for the full design, architecture, and
build roadmap.

## Status

🟡 **0.1.0 — skeleton.** Proves the deploy loop and verifies the two bits of
infrastructure everything depends on: the **ComfyUI connection** and the **shared
builds folder being read/write**. UI is a provisional static shell (left sidebar +
pinned status); React lands in 0.2.x once the style references are in.

Versioning is `0.<phase>.<iteration>` — see `VERSION`.

## Deploying 0.1.0 on UR1

Prerequisites on the ComfyUI container (already done):
- Unraid → container edit → **Add another Path**: host
  `/mnt/user/data-and-backups/blender-and-comfyui-output/comfyui-builds` → container
  `/builds`, Read/Write.
- `05-comfy-ui/parameters.txt` contains `--output-directory /builds`.

### What to copy

**Copy the WHOLE project folder** — not just `docker/`, not just `appdata/`.

This differs from `comfyui-mcp` / `blender-mcp`, where only `docker/` is copied.
Those pull a **prebuilt** image; Persona Forge currently **builds from source**, so
the compose build context (the repo root) must be present. `appdata/` is a
*subfolder* of the project — it is not copied anywhere separately.

Resulting layout on UR1:

```
/mnt/user/appdata/persona-forge/     <- copy the whole repo folder here
├── docker/
│   ├── docker-compose.yml           <- point Docker Compose Manager at THIS file
│   └── .env                         <- you create this from .env.example
├── backend/                         <- required (build context)
├── frontend/                        <- required (build context)
├── workflows/                       <- required (build context)
├── VERSION
└── appdata/                         <- persistent data, mounted into the container
                                        as /appdata (sqlite db + prompt history)
```

(Once we publish an image to GHCR, only `docker/` + `appdata/` will be needed —
see the note at the top of `docker/docker-compose.yml`.)

### Steps

1. Copy the whole `persona-forge` folder to `/mnt/user/appdata/persona-forge/`.
2. `cd docker && cp .env.example .env`, then check the values — especially
   `BUILDS_HOST_PATH` (must be the **same host path** mapped into ComfyUI as
   `/builds`) and `COMFYUI_URL`.
3. In the Unraid **Docker Compose Manager** addon, add a stack pointing at
   `/mnt/user/appdata/persona-forge/docker/docker-compose.yml` → **Compose Up**.
4. Open `http://192.168.1.33:8890`.

**Expected result:** both dots in the sidebar green — *ComfyUI* showing latency and
GPU, *Builds* showing `read/write`. If Builds shows red, the bind mount or its
permissions are wrong, and no later phase will work until it's green.

## Deploy model

This project follows the UR1 convention: development happens in this local repo;
the **`appdata/` folder is copied to `/mnt/user/appdata/persona-forge/` on UR1**
and the stack is built with the **Docker Compose Manager addon**. ComfyUI
(UR1:9000) and Ollama are external services the app talks to — it does not bundle
ComfyUI.

## Structure

| Path | What |
|---|---|
| `backend/` | FastAPI orchestration + state (to be built) |
| `frontend/` | React UI (to be built) |
| `appdata/` | Persistent config/db/datasets/loras/output — **the folder copied to UR1** |
| `docker-compose.yml` | The stack definition |
| `docs/` | Design notes |
