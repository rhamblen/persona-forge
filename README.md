# Persona Forge

Repo: https://github.com/rhamblen/persona-forge

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

1. Copy this whole folder to `/mnt/user/appdata/persona-forge/` on UR1.
2. `cp .env.example .env` and check the values — especially `BUILDS_HOST_PATH`
   (must be the **same host path** mapped into ComfyUI) and `COMFYUI_URL`.
3. In the Unraid **Docker Compose Manager** addon, add the stack pointing at that
   folder and **Compose Up** (builds the image).
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
