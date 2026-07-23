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

⬜ **Planning.** No code yet — this repo currently holds the plan. First milestone
(M0) is the deploy skeleton.

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
