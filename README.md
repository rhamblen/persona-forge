# persona-forge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/ghcr.io-persona--forge-2496ed.svg)](https://github.com/rhamblen/persona-forge/pkgs/container/persona-forge)
[![Python](https://img.shields.io/badge/python-3.12-brightgreen.svg)](https://www.python.org)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-required-purple.svg)](https://github.com/comfyanonymous/ComfyUI)

Build **SillyTavern expression sets — face *and* posture** — for your own characters,
using your own ComfyUI. A guided web app takes you from a prompt to a trained
per-character LoRA and a full sprite set, with **Ollama** on hand to edit prompts in
plain language and **full rollback** so an approved prompt is never lost.

## Why?

Sometimes when using SillyTavern you want to create your own characters with your own
images — but you don't want a static image, you want expressions. Live2D and VRM are
complicated to build, and facial expressions alone are easy to misread or simply not
notice, especially across 28 of them. Using **posture** as well makes the character far
more readable.

Posture is also why this trains a LoRA rather than just prompting: varying the body
means re-generating it, and only a per-character LoRA keeps it recognisably the same
person. (IPAdapter alone was tested and drifts — hair, outfit and proportions wander
shot to shot.)

## Features

- **Prompt Studio** — pick a checkpoint, refine a prompt against live previews, then
  **sign off a baseline** that can never be lost.
- **Version history like a VCS** — every edit appends a new version with a diff of what
  changed; roll back to any point. Nothing is ever overwritten or deleted.
- **Persona library** — personas persist and reload. **Clone** one to vary it: the same
  character skiing *and* lazing on a beach. Clones record their parent so a trained LoRA
  can be reused instead of retrained.
- **Dataset builder** — generate a batch, pick the ones that look like the same person,
  top up until you have enough _(phase 4)_.
- **Per-character LoRA training** on your own GPU _(phase 5)_.
- **Pose / expression sets** — the 28 SillyTavern expressions with posture variation,
  tweakable one sprite at a time _(phase 6)_.
- **Ollama natural-language prompt editing** _(phase 3)_.
- **Logs tab** — filter by level and by `boot` / `integration` / `process` / `local`;
  also on stdout and in a rolling file so boot history survives a restart.
- Runs **entirely on your LAN**. ComfyUI stays external — this does not bundle it.

## Quick Start

**Prerequisites** — a reachable ComfyUI, and a folder both containers can share:

1. In Unraid, edit the ComfyUI container → **Add another Path**: your shared builds
   folder → container `/builds`, Read/Write.
2. Add `--output-directory /builds` to ComfyUI's `parameters.txt`, and restart it.

**Install** — only the `docker/` folder goes on the server:

1. Copy **`docker/`** to `/mnt/user/appdata/persona-forge/docker/`.
2. Edit `docker/.env` — set `COMFYUI_URL`, `BUILDS_HOST_PATH`, `DB_HOST_PATH` and
   `LOGS_HOST_PATH` (all paths **absolute**). It ships ready to use, so there is no
   file to rename.
3. Unraid **Docker Compose Manager** → point at
   `/mnt/user/appdata/persona-forge/docker/docker-compose.yml` → **Compose Up**.
4. Open `http://<server>:8890`.

The image is pulled from GHCR — no source and no build on the server. To update:

```bash
docker compose pull && docker compose up -d
```

On the server you end up with `db/` and `logs/` sitting alongside `docker/`:

```
/mnt/user/appdata/persona-forge/
├── docker/   compose + .env  (the only folder you copy)
├── db/       sqlite: personas, prompt history
└── logs/     rolling log file
```

**Check it worked:** both dots in the sidebar should be green — *ComfyUI* showing
latency and GPU, *Builds* showing `read/write`. If Builds is red, the bind mount or its
permissions are wrong and nothing downstream will work.

## Configuration

All settings live in `docker/.env`:

| Variable | Default | What it does |
|---|---|---|
| `COMFYUI_URL` | `http://192.168.1.33:9000` | Where ComfyUI lives |
| `BUILDS_HOST_PATH` | — | **Required.** Host path of the shared builds folder. Must be the same path mapped into ComfyUI as `/builds`. |
| `DB_HOST_PATH` | — | **Required, absolute.** Where the sqlite db + prompt history live, e.g. `/mnt/user/appdata/persona-forge/db` |
| `LOGS_HOST_PATH` | — | **Required, absolute.** Rolling log file, e.g. `/mnt/user/appdata/persona-forge/logs` |
| `PF_PORT` | `8890` | Published port |
| `PUID` / `PGID` | `99` / `100` | Ownership applied to build folders, so the ComfyUI container can write into them |
| `TZ` | `Europe/London` | Timezone |

Each persona gets a build folder in the shared root:

```
<builds-root>/<persona>/
├── lora/     trained LoRA for this character
└── images/   generated sprites
```

Finished sprites are **staged, never auto-copied** into SillyTavern — moving them into a
character's `expressions/` folder stays a deliberate manual step.

## Repo layout

| Path | What |
|---|---|
| `docker/` | The stack — **the only folder deployed to the server** |
| `backend/` | FastAPI app: orchestration, state, prompt versioning |
| `frontend/` | Web UI |
| `workflows/` | ComfyUI API-format templates + parameter manifests |
| `docs/` | Design notes and UI references |
| `PROJECT_PLAN.md` | Architecture, phases and open decisions |
| `CHANGELOG.md` | What changed in each release |

## Status

**Phase 2 of 7** — Prompt Studio is usable; dataset, LoRA training and pose sets are
still to come. See the roadmap in [PROJECT_PLAN.md](PROJECT_PLAN.md) and the release
history in [CHANGELOG.md](CHANGELOG.md).

Versioning is `0.<phase>.<iteration>` — the middle digit is the phase, the last
increments with each update inside it.

## Documentation

- [PROJECT_PLAN.md](PROJECT_PLAN.md) — goals, architecture, phases, open decisions
- [CHANGELOG.md](CHANGELOG.md) — per-release detail
- [docs/ui-style.md](docs/ui-style.md) — UI design direction

## Related projects

- [blender-mcp](https://github.com/rhamblen/blender-mcp) — the 3D/VRM avatar track
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — the generation engine

## License

MIT — see [LICENSE](LICENSE).
