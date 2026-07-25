# AI Context — cold-start orientation

> **Latest session handover:** `docs/handover-2026-07-25.md` — shipped 0.6.2→0.7.1 (LoRA-driven
> poses, training timer, **generic job engine + `lora_build` overnight build**, prompt-studio
> fixes), moved aux GPU containers off the 3090. **0.7.2 + 0.7.3 fixed the weak/pose-locked LoRA**
> at the dataset root: 0.7.2 = pose/framing variety, 0.7.3 = close-up framings + varied
> expressions. Dataset side now complete; last lever is a higher training-step default. Read it
> first.


Dense factual map for the next AI session. Not for end users. Read this first, then
`PROJECT_PLAN.md` for the full spec. Keep this file current **every release**.

> **2026-07-25 — LoRA tab folds into Poses/Phase-6 development** (one stream, not two).
> The joint next step is wiring the trained LoRA into pose generation. See
> `docs/handover-lora-into-poses.md`.

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
- **0.3.0:** AI prompt assistant (Ollama Replace/Modify), Ollama sidebar Connect/Unload +
  idle auto-unload, container Start/Restart via socket proxy, preview zoom, anime-first
  checkpoint default.
- **0.3.1:** Logs tab reskinned to the house terminal-style standard (matches the
  esp32-shutter-hub web UI); frontend served `Cache-Control: no-cache` so deploys are never
  served stale (fixes a browser caching a new page against old JS).
- **0.3.2:** AI suggestions show a client-side word-level diff (added=green, removed=red
  strikethrough) so edits — and any word the local model dropped — are visible before saving.
- **0.3.3:** the AI diff is now per-change accept/reject — each change has a ✕ to revert
  just that span (↺ to re-apply); hand-editing a field retires its diff.
- **0.3.4:** Modify prompt tightened to a strict verbatim/minimal-edit instruction — it no
  longer drops or rewrites unrelated text (was deleting whole sentences for a one-word
  change). See `_MODE_HINT["modify"]` in `ollama.py`.
- **0.4.0:** Phase 4 — Dataset tab. Queue a batch of candidates (Generate 30 / +10) at
  fresh seeds, pick same-person images in a grid, target N + progress. Non-blocking:
  `dataset_jobs` table + reconcile-from-history into `images` (kind='dataset'). Endpoints
  under `/api/projects/{id}/dataset`. `projects.dataset_target` column added.
- **0.4.1:** dataset thumbnails have a hover ⤢ zoom badge that opens the shared lightbox
  (reuses `openLightbox`); zoom is separate from click-to-select.
- **0.5.0:** Phase 5 foundation — LoRA tab (trigger word, staged status, trained LoRAs) +
  dataset staging. Endpoints under `/api/projects/{id}/lora`. `projects.trigger_word` column.
- **0.6.0 (current):** Phase 6 — Poses tab (parallel with LoRA phase; tags interleave).
  Add poses / presets (Starter 8, or the 28 ST expressions), Generate all (queued +
  reconcile via `poses` table), select→zoom→edit (manual or `ollama.revise()`)→regenerate one.
  Endpoints under `/api/projects/{id}/poses`. Renders base prompt + pose `modifier` via the
  base-character `expression` param; will use the trained LoRA once Phase 5 completes.
- **0.6.1:** Phase 6 — Export on the Poses tab. Mattes each rendered
  pose to a transparent PNG (BEN2) named for ST (exact expression names verbatim, else
  slugified, de-duped), staged to `<build>/export/<Character>/`, never auto-copied. New
  `workflows/bg-remove.json` (BEN2 + WAS Image Save prefix_as_filename), `export_jobs` table,
  `GET/POST /api/projects/{id}/poses/export`.
- **0.6.2 (current):** Phase 6 — **LoRA-driven poses** (the joint deliverable). New
  `workflows/pose-with-lora.json` (`CheckpointLoaderSimple → LoraLoaderModelOnly → KSampler`,
  trigger word prepended); Poses tab **Character LoRA** selector (+ strength); endpoints
  `GET /pose-config`, `POST /pose-lora`; `projects.pose_lora`/`pose_lora_strength`. Pose renders
  fall back to `base-character` when no LoRA is selected. Graph validated end-to-end
  (status=success on ComfyUI 0.28.0). Also: **training timer + ETA** (start time + run duration
  logged at `info`; elapsed/ETA on the LoRA tab; `train_started_at`/`train_steps`/
  `last_train_seconds`/`last_train_steps` columns), and the export panel relabelled
  **"Export to builds folder"**.
- **0.5.1:** logging overhaul — added a `verbose` level (below debug) and
  instrumented the pipeline throughout (not just boot). Log-level convention:
  **verbose** = cross-system handshakes / per-file share copies / polls (integration+local);
  **debug** = step detail; **info** = milestones (process); **warn** = recoverable (missing
  file, failed render); **error** = failures. `api` category logs every inbound request at
  verbose. Boot handshakes ComfyUI+Ollama. UI: VERBOSE chip + min-level; verbose reaches
  stdout too. When adding new pipeline steps, log to this convention.

## Phase 5 (LoRA trainer) — settled approach & knowns

- **Training is a ComfyUI workflow, not a separate container.** The instance has native
  training nodes: `TrainLoraNode` (model, latents, positive, batch_size, steps, learning_rate,
  rank, optimizer/algorithm/dtypes COMBOs → LORA_MODEL), dataset loaders
  (`LoadImageTextDataSetFromFolder`, `LoadImageDataSetFromFolder`), and `SaveLoRA`
  (lora, prefix, steps). Captioning: `Florence2Run` (tasks incl. `caption`, `detailed_caption`,
  `prompt_gen_tags`), `BLIP`. `python_module comfy_extras.nodes_dataset`.
- **The dataset loader reads ONLY from ComfyUI's `input/` dir** (a COMBO of its subfolders,
  e.g. `3d`). `/builds` is NOT under input. The app stages a dataset by **uploading images via
  ComfyUI's HTTP `/upload/image`** (`type=input`, `subfolder=pf-<slug>`) — validated: the
  uploaded folder appears in the loader COMBO instantly. **No extra mount needed.**
- **Captioning decision (user, 2026-07-24): trigger word + light caption** — caption each
  image `pf_<slug>, <short Florence2 caption>`; the LoRA binds to the trigger token.
- **Training VALIDATED + shipped (0.5.2).** Graph `workflows/lora-train.json`:
  `CheckpointLoaderSimple` → `LoadImageDataSetFromFolder(folder=pf-<slug>)` →
  `ImageListToImageBatch` → `VAEEncode` + `CLIPTextEncode(trigger)` → `TrainLoraNode`
  (offloading=True, bf16, AdamW/MSE) → `SaveLoRA(prefix=<slug>/lora/<trigger>)`. Confirmed by a
  real 16-step run (status=success). Endpoint `POST /api/projects/{id}/lora/train`;
  `projects.train_prompt_id`/`train_status`; reconcile in the lora GET.
- **VRAM:** training OOMs if ComfyUI/Ollama hold the 3090 — the train endpoint unloads Ollama
  + calls `comfy.free_memory()` (ComfyUI `/free`) first. `offloading=True` in the graph.
  **Known (0.6.2):** UR1's 3090 is *shared* with other GPU containers (ollama, chatterbox-st,
  immich:cuda, a-eye) which can hold ~13 GB; ComfyUI's free can't evict them, so training OOMs
  under aux load even though its own free works. Confirmed live (after ComfyUI unload, 3090
  still showed only ~10.7 GB free). Interim workflow: **stop the aux GPU containers before a
  training run**; the idle RTX 3060 (12 GB) is the eventual home for them.
- **LoRA loadability:** `SaveLoRA` writes to the OUTPUT dir (`/builds/<slug>/lora/`), NOT
  `models/loras` — confirmed (trained lora not in the loras dropdown). User must add
  `loras: /builds` to ComfyUI `extra_model_paths.yaml` + restart ComfyUI to load them.
- **Captions (0.5.3): per-image Florence-2, VALIDATED.** Inline in `lora-train.json`:
  `Florence2ModelLoader` + `Florence2Run(task=caption)` (maps over the image list) →
  `StringConcatenate(string_a=trigger, delimiter=", ", string_b=caption)` → per-image
  `CLIPTextEncode` → `TrainLoraNode`. Confirmed `TrainLoraNode` accepts a per-image
  conditioning LIST matched to the batched latent. **Gotcha:** `AddTextPrefix` is
  `INPUT_IS_LIST` and collapses the list (breaks CLIPTextEncode) — use `StringConcatenate`
  (auto-maps). `SaveImageTextDataSetToFolder` writes to OUTPUT not INPUT, so the two-step
  captioned-folder path doesn't work — inline captioning avoids it.
- **Test artifacts left in ComfyUI input:** `pf-uploadtest`, `pf-traintest` (harmless).
- ✅ **Phase 5 (LoRA trainer) COMPLETE** through 0.5.3 — tab, trigger word, dataset staging,
  native `TrainLoraNode` training (validated), per-image Florence-2 captioning. **Development
  of the LoRA tab is handed to the Poses/Phase-6 session** as of 2026-07-25 (one stream). The
  remaining LoRA-related work — **wiring the trained LoRA into pose generation** — is Phase-6
  work. See `docs/handover-lora-into-poses.md`.
- ✅ **Phase 6 (pose/expression studio)** — poses grid/edit/regenerate (0.6.0), sprite
  export (0.6.1), and **LoRA-driven pose generation (0.6.2)** all done. The joint
  LoRA-into-poses deliverable is complete: pose renders load the project's trained LoRA with
  the trigger word prepended (`pose-with-lora` workflow), selectable per project.
- ✅ **Phase 7 (orchestration) — job engine (0.7.0).** Generic in-process background worker
  (`jobs.py` + `jobs` table): one asyncio loop drains a persisted FIFO, advancing the running
  job stage-by-stage, **browser-independent + resume-safe** (progress in `stage`+`state_json`).
  Handlers register per `kind`; first is **`lora_build`** = train → auto-apply LoRA → render 28
  expressions, restarting ComfyUI (via docker proxy) to bind a fresh LoRA, degrading to base
  poses if it can't. Endpoints `POST/GET /projects/{id}/jobs`, `GET /jobs`, `GET/POST
  /jobs/{id}[/cancel]`; "Build overnight" panel on the LoRA tab. Deferred to Phase F: the
  multi-character **add-to-queue** cast builder + concurrency lanes — both ride this engine
  (that's the whole point of building it generic). Lorebook/campaign/ingest (Phase E/F/G) also
  plug in as handlers. Manual Train/Generate-all still work (engine reuses the same helpers).
- **0.7.2:** Phase 7 — **pose/framing variety in the Dataset Builder** (fix for the *pose-locked*
  half of weak LoRAs). `dataset_generate` cycles candidates across framings via the base-character
  `expression` suffix + a fresh seed; rotation continues across batches (offset by existing
  `dataset_jobs` count). `pose_variety` bool (default true) + a Dataset-tab toggle.
- **0.7.3:** Phase 7 — **close-up framings + varied expressions in the dataset** (fix for the
  *weak-face* half). Two-axis variety: `DATASET_FRAMINGS` (12; ~⅓ close-up/bust so the face has
  enough pixels — the rest full body/pose) × `DATASET_EXPRESSIONS` (10; neutral, happy, sad, angry,
  shocked, embarrassed, alluring, flirtatious — neutral-weighted). Combined per candidate by
  `_dataset_variation(n)` (`n % 12` framing, `n % 10` expression → 30 unique pairs in a 30-batch).
  Varied expressions **decouple** from identity because the trainer captions each image
  (Florence-2). Same `expression`-suffix injection — no new graph, no schema change. Toggle
  relabelled "Framing, pose & expression variety." **Caveat:** a style prompt that hard-codes
  "full body" can fight close-ups; the app doesn't rewrite prose.
- **Remaining:** 0.7.x hardening · 1.0 release. The **dataset side of the weak-LoRA fix is now
  complete** (0.7.2 pose + 0.7.3 framing/expression). Last recipe lever: **raise the automated
  training-step default to ~1500-2500** (0.7.x). Optional polish: reuse-parent-LoRA for clones
  (`parent_project_id`), training loss/step readout.

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
