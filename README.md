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

Then:

1. Copy **this whole project folder** to `/mnt/user/appdata/persona-forge/` on UR1.
   (The whole folder is needed for now because the stack **builds from source** —
   the compose build context is the repo root. Once we publish an image to GHCR,
   only `docker/` + `appdata/` will be needed; see the note in
   `docker/docker-compose.yml`.)
2. `cd docker && cp .env.example .env`, then check the values — especially
   `BUILDS_HOST_PATH` (must be the **same host path** mapped into ComfyUI) and
   `COMFYUI_URL`.
3. In the Unraid **Docker Compose Manager** addon, add a stack pointing at
   `/mnt/user/appdata/persona-forge/docker/docker-compose.yml` and **Compose Up**.
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
