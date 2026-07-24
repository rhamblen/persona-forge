# AI Context — cold-start orientation

Dense factual map for the next AI session. Not for end users. Read this first, then
`PROJECT_PLAN.md` for the full spec. Keep this file current **every release**.

## What it is

Persona Forge is a self-hosted web app that turns a character description into a
SillyTavern-ready expression/pose set, via ComfyUI, without hand-driving ComfyUI. The
pipeline: **Prompt Studio → dataset build → per-character LoRA → pose/expression set**,
with a local LLM (Ollama) for natural-language prompt authoring. Everything runs on the
LAN; no Claude/Anthropic in the runtime loop.

- Repo: https://github.com/rhamblen/persona-forge · deployed on UR1.
- Stack: FastAPI backend + a static vanilla-JS frontend (no build step) + SQLite.
- The product's core bet: a **per-character LoRA**. Posture variation needs the whole
  body re-generated, and only a LoRA keeps it the same person (IPAdapter proved too
  drifty — see `PROJECT_PLAN.md` §9).

## How to work here

- **Claude never builds or deploys containers.** Edit the local repo → tell the user what
  to copy → the user copies `docker/` and runs Docker Compose Manager on UR1 → Claude
  verifies over HTTP. Writes into `/mnt/user/appdata/...` over SMB are denied (root-owned).
- **Only `docker/` goes on the server.** The image is built by GitHub Actions and pulled
  from GHCR — never built on UR1.
- **Versioning is `0.<phase>.<iteration>`** — middle digit = current phase. Every release
  gets a CHANGELOG entry, a RELEASE_NOTES.md rewrite, a git tag, and a real GitHub Release
  (see "How to publish" below).
- **Prompts are prose, not Danbooru tags.** The user writes prose; tag rewrites drop
  detail and break garments. Only mechanical edits are allowed (strip expression words,
  fix smart punctuation).
- **Expression words must never sit in the CHARACTER field** — a baked-in smile leaks into
  anger/grief. Identity and expression are kept separate everywhere, including the Ollama
  system prompt.
- **Keep this doc updated** when decisions, infrastructure, or the API change.

## Infrastructure (all UR1 containers)

| Service | Container | Reachable at |
|---|---|---|
| ComfyUI | `stable-diffusion-ComfyUI` | `http://192.168.1.33:9000` |
| Ollama | `ollama` (br0 macvlan, own LAN IP) | `http://192.168.1.32:11434` |
| Persona Forge | `persona-forge` | `http://192.168.1.33:8890` |
| Docker proxy | `persona-forge-docker-proxy` | internal only (`docker-ctl` net) |

- Shared builds folder: host `/mnt/user/data-and-backups/blender-and-comfyui-output/comfyui-builds`
  → mounted `/builds` in **both** ComfyUI and Persona Forge. A project == a folder here
  (`<slug>/lora/` + `<slug>/images/`), owned `PUID:PGID` (99:100) so ComfyUI can write.
- App state: `/mnt/user/appdata/persona-forge/{docker,db,logs}` — `db/` + `logs/` are
  **peers** of `docker/` (absolute compose binds; Unraid's Compose Manager doesn't
  reliably set the project dir).
- Ollama has `llama3.1` (default), `mistral`, `phi3`, `codellama`, `deepseek-r1`,
  `minicpm-v`. The 3rd Unraid box **URHP1 is a backup file server only** — no compute.

## File map

| Path | Contents |
|---|---|
| `backend/app/main.py` | FastAPI app: all routes, project/version orchestration, generation |
| `backend/app/comfy.py` | ComfyUI HTTP client (submit/wait/outputs), `queue_size`, checkpoint default resolver |
| `backend/app/ollama.py` | Ollama client: `suggest_prompt` (Replace/Modify), `status`/`warm`/`unload` |
| `backend/app/docker_ctl.py` | Start/restart ComfyUI+Ollama via the scoped socket proxy |
| `backend/app/db.py` | SQLite schema + `connect()` |
| `backend/app/logs.py` | Levels + categories, ring buffer + rolling JSONL |
| `backend/app/workflows.py` | Workflow templates + parameter manifests (node IDs not hardcoded) |
| `frontend/index.html`, `app.js`, `style.css` | The SPA (no build step); served as static files |
| `workflows/base-character.json` + `.manifest.json` | The base-character API graph + its manifest |
| `docker/docker-compose.yml` | The stack: persona-forge + docker-socket-proxy + networks |
| `docker/.env` | Tracked, non-secret config (the only `.env` git tracks) |
| `PROJECT_PLAN.md` | Master spec + phased roadmap (this repo's "project brief") |
| `docs/ui-style.md` | UI design tokens from the user's esp32-shutter-hub card |
| `CHANGELOG.md` / `RELEASE_NOTES.md` | Keep-a-Changelog / current release body (rewritten each release) |

## API surface

- **Health/status:** `GET /api/health`, `/api/comfyui/status`, `/api/storage/status`.
- **Models/workflows:** `GET /api/models?kind=` (returns `default`), `/api/workflows[/{id}]`.
- **AI assistant:** `GET /api/ai/status` (reachable/loaded), `POST /api/ai/warm`,
  `/api/ai/unload`, `/api/ai/suggest-prompt` (`{instruction, mode: replace|modify, character, style, negative}`).
- **Container control:** `GET /api/containers/status`, `POST /api/containers/{key}/start`,
  `/api/containers/{key}/restart?force=` (`key` ∈ `comfyui`, `ollama`).
- **Projects/versions:** `POST/GET /api/projects[/{id}]`, `.../versions`, `.../signoff`,
  `.../rollback/{version_id}`, `.../clone`, `.../generate`, `.../repair-permissions`.
- **Images/builds:** `GET /api/image`, `/api/builds`. **Logs:** `GET /api/logs[/persisted]`.

## Data model (SQLite, append-only versioning)

- `projects` (name, slug, `current_version_id`, `parent_project_id` for clones).
- `prompt_versions` — **append-only**; nothing is ever edited or deleted. Fields:
  character, style, negative, checkpoint, seed, source, note, signed_off. Rollback creates
  a *new* version copying an old one. Rendered in the UI as a VCS-style rail with per-field
  diff tags. Each build folder also gets a self-describing `persona.json` sidecar.
- `images` (project_id, version_id, filename, subfolder, kind).

## Build phases

- **Done:** Phase 1 (deploy loop, infra checks) · Phase 2 (projects=build folders,
  append-only versioning + sign-off + rollback, persona clone reusing parent LoRA,
  persona.json, logs tab, workflow templates/manifests).
- **0.3.0 (current):** AI prompt assistant (Ollama Replace/Modify), Ollama sidebar
  Connect/Unload + idle auto-unload, container Start/Restart via socket proxy, preview
  zoom, anime-first checkpoint default.
- **Remaining:** 0.4 dataset builder · 0.5 LoRA trainer · 0.6 pose/expression studio ·
  0.7 hardening · 1.0 release.

## Track A note (separate from the app)

The **28-expression ComfyUI workflows** (root `workflows/README.md`, outside this repo)
are a working, documented deliverable: base char sampled once, `FaceDetailer` repaints
only the face per expression for pixel-perfect alignment. Driven by WAS `Number Counter`
with **batch count = 28** (NOT For-Loop, NOT `control_after_generate`). BEN2 for background
removal. These predate and inform the app; the app will fold in similar logic at Phase 6.

## Gotchas

- **Backend talks to ComfyUI/Ollama over native HTTP, not MCP.** MCP is an LLM
  tool-calling wrapper; an app must not add a JSON-RPC hop.
- **Checkpoint default is resolved, not positional.** ComfyUI lists checkpoints in folder
  order, so option 0 is `!first/...` (photoreal). `comfy.pick_default_checkpoint` picks
  exact `DEFAULT_CHECKPOINT` → first `PREFERRED_CHECKPOINTS` match → position 0.
- **Ollama Modify must never destroy** an untouched field — if the model returns "", the
  current value is kept (`ollama.suggest_prompt`).
- **Container control is opt-in and scoped.** PF talks to `tecnativa/docker-socket-proxy`
  (CONTAINERS + ALLOW_START + ALLOW_RESTARTS, POST=0, socket read-only, internal network),
  **never** the raw socket. Unset `DOCKER_PROXY_URL` → feature + UI disappear. Only
  recovers a stopped container on a live host; if UR1 is down, so is PF. ComfyUI restart is
  refused while its queue is busy unless `force=true`.
- **The frontend has no live dirty-tracking** — `formValues()` is read on demand at
  Generate/Save/Sign-off. Top-level `addEventListener` calls run at parse time, so every
  referenced element ID must exist in `index.html` or the script breaks.
- **ComfyUI env is partly broken** — a `flash_attn` ABI mismatch kills rgthree,
  ComfyUI_essentials, inpaint-nodes, PuLID and some core `comfy_extras`. `pip uninstall
  flash_attn` is the fix. Doesn't affect the app's base-character path.

## How to publish a new version

1. Bump `VERSION`; update `CHANGELOG.md` (Keep-a-Changelog) and rewrite `RELEASE_NOTES.md`
   for the new tag; refresh this doc + `PROJECT_PLAN.md` status if the phase moved.
2. `git commit` (`feat(vX.Y.Z): …`, end body with the required `Co-Authored-By` trailer).
3. `git tag -a vX.Y.Z -m "…"` then `git push origin main --tags`.
4. The tag push triggers `.github/workflows/publish-image.yml` → builds and pushes
   `ghcr.io/rhamblen/persona-forge:X.Y.Z` **and** `:latest`. Confirm the run is green
   (`gh run list --workflow=publish-image.yml`).
5. Create the GitHub Release explicitly:
   `gh release create vX.Y.Z --title "…" --notes-file RELEASE_NOTES.md`.
6. User deploys on UR1: re-copy `docker/`, review `docker/.env`, `docker compose pull &&
   docker compose up -d`. Claude then verifies over HTTP.
