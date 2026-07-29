# AI Context — cold-start orientation

> **2026-07-30 — v0.8.10: pose families, per persona.** `axis_pose_families` maps an
> emotion axis — or one TIER of it — to a posture family (`pose_library.family`), so an
> intensity ladder can change posture as it climbs. `project_id IS NULL` rows are the global
> defaults and a persona's rows overlay them key by key: **two characters must not be forced
> into the same pose for the same emotion**. Resolution order at render time is per-pose
> skeleton → persona default → persona family → global family → none; an explicit choice
> always outranks a family or assigning one would silently overwrite per-pose work. The
> within-family pick is a NAME HASH and deliberately meaning-blind — it will put "head
> buried" on Elation — so `entry_id` pins a tier to one entry and the hash is only the
> fallback. Family-resolved skeletons are re-staged (overwrite) every render rather than
> cached by id, because the PNG derives from keypoints the user can edit. Two authoring
> facts: head tilt IS encodable (ears→eyes→nose vertical ordering) unlike palm facing, and
> seated/crouching figures need foreshortened legs plus a compressed torso or they render as
> standing figures regardless of leg placement.
>
> **2026-07-29 — v0.8.9: ControlNet defaults are 1.0/0.9, and COCO-18 can't encode palms.**
> Measured A/B (same seed + skeleton): `strength 0.7 / end 0.7` let a strength-1.0 character
> LoRA override the skeleton entirely — the pose was ignored, which reads as "ControlNet is
> broken". 1.0/0.9 obeys it; end stays short of 1.0 so identity settles and the skeleton's
> black background doesn't bleed in. **Model choice mattered more than any dial**:
> `xinsir-openpose-sdxl` on the NoobAI-XL checkpoint bled the black background in and shifted
> colours red; `noobai-openpose-sdxl` (checkpoint-matched) did neither. Also measured and
> **disproven**: stripping stance words from the prompt did not help, so don't "fix" prompts
> for pose control. Column defaults need a matching UPDATE migration or existing personas
> silently keep the old values. **COCO-18 has no hand joint or wrist rotation** — palm facing
> is unrepresentable in a skeleton; it lives in `prompt_hint`, which is why the three
> arms-wide variants differ only by forearm angle. `seed_pose_library(force=True)` now tops
> up by name rather than replacing built-ins, since the catalogue grows between releases.
>
> **2026-07-29 — v0.8.8: ControlNet needs BOTH a skeleton and a model.** `_pose_cn_cfg()`
> returns `None` (render with no structural control) if either is missing. The failure mode
> that caused: skeleton picked, no model selected → poses render from the prompt alone,
> status `done`, zero errors, and the chosen figure silently discarded. When debugging "the
> pose didn't change", check `projects.pose_controlnet` BEFORE suspecting the workflow — a
> clean log is not evidence ControlNet ran. That case now logs at `warn` and both the
> skeleton picker and the panel summary say it. Deliberately **no auto-select**: 24
> ControlNets are visible to ComfyUI and a wrong pick yields confident anatomical garbage.
>
> **2026-07-29 — v0.8.7: the GPU is SHARED; training gates on it.** UR1's 3090 also serves
> Ollama (192.168.1.32 — other LAN apps load their own models into it) and Immich's CUDA ML
> server. A 1500-step rank-16 SDXL run peaks at **~17.8 GB reserved**, so ~8 GB of foreign
> tenancy is enough to OOM it — and the OOM surfaces in the **VAE encode** (`group_norm`),
> not the training loop, which misleads. Two things follow. (1) The pre-flight calls
> `ollama.unload_all()`, not `unload()`: unloading only `OLLAMA_MODEL` was a silent no-op
> whenever another app's model held the VRAM. (2) `_require_train_vram()` blocks below
> `MIN_TRAIN_VRAM_GB` (default 18) — it **fails open** if ComfyUI can't be read, deliberately.
> Measured fact worth not re-deriving: ComfyUI's `/system_stats` `vram_free` is the
> **device-wide** free figure, so it does see other tenants (17.0 GB free while nvidia-smi
> showed 8.4 GB held; 24.9 GB once released). Note `get_gpu_metrics` on the Unraid MCP is
> **cached** — two calls can return an identical stale timestamp, which will fake a
> disagreement with ComfyUI's live number.
>
> **2026-07-29 — v0.8.6: images are read off `/builds`, not fetched from ComfyUI.** `GET
> /api/image` now serves `type=output` straight from the shared builds mount
> (`_builds_path()` resolves + guards the path), and only proxies ComfyUI's `/view` for
> what isn't there — `type=input`/`temp`, which live in ComfyUI's own directories. That is
> why the Dataset, Poses and sheet grids stay browsable with ComfyUI stopped. Don't
> "simplify" this back to a straight proxy: both containers bind the *same host path* as
> `/builds`, so the local read is the authoritative one and the HTTP hop was pure coupling.
>
> **2026-07-29 — v0.8.5: the pose library (H3b).** `pose_library` holds **normalised COCO-18
> keypoints**, not images — `skeleton.py` renders them at any size, which is why the picker has
> thumbnails and why the H3f editor is possible. Seeded lazily from `skeleton.STARTER_POSES`
> and **not reseeded once anything exists** (emptying it deliberately must stay empty). Two
> per-entry fields do real work: `prompt_hint` is appended to the render prompt (a skeleton
> encodes a grip but not the sword, and it disambiguates a front-view kneel from a short
> stand), and `face_visible=false` turns the face pass off by default — an explicit per-pose
> setting still wins. Two measurements not to re-derive: **DWPose returns "no person detected"
> for every kneeling/sitting/lying anime figure** (two detectors tried), so hand-authored
> keypoints are the primary source and extraction is standing-only; and **a standing-only
> character LoRA overpowers the skeleton** — a kneeling skeleton at strength 0.7 renders
> standing with the LoRA and kneels without it, which is the empirical case for H3c. When
> authoring poses, render a contact sheet first: head-size and ankle-below-knee errors both
> shipped past code review and were only visible as pictures. Occluded joints are `None`.

> **2026-07-28 — v0.8.4: structural pose control (H3a).** Pose renders are now **two passes
> carrying three tunable layers** — *base* (prompt/LoRA/seed) and *body* (skeleton) both settle
> in pass 1; *face* is pass 2. **Keep the pass-1 image** (`poses.base_filename`): re-running the
> face against it is the cheap loop (14s vs ~104s) and is what lets an expression be retried
> without disturbing an approved body. Pose status now runs `pending → facepass → done`; treat
> `facepass` as in-flight everywhere `pending` is (it is why `poses_list.pending` counts both).
> ControlNet is **spliced**, not templated — `workflows.apply_controlnet()` inserts
> LoadImage/ControlNetLoader/ControlNetApplyAdvanced and repoints conditioning consumers, and
> every workflow declaring a `controlnet` manifest block gets it (so H3c's dataset work needs no
> new template). It composes with the LoRA chain because ControlNet touches **conditioning** and
> LoRAs touch **model/CLIP**. Per-pose columns are **nullable = inherit the persona default** —
> preserve that, it is how one dial moves a whole set. Two measured facts not to re-derive:
> face-pass denoise **0.60** (0.45 does nothing, 0.75 destroys the face), and **a base-SDXL
> checkpoint renders a flat face at every denoise** — proven as a 2×2 against the character LoRA,
> which is *not* the limiting factor. Also: `comfy.list_models()` now **raises** on an unknown
> kind instead of falling back to checkpoints; FaceDetailer's `wildcard` input is required with
> no schema default (send `""`). Full design + measurements: `docs/pose-control.md`.

> **2026-07-28 — v0.8.3: admin tools (deletion).** `DELETE /api/projects/{id}?delete_files=`,
> `DELETE /api/versions/{id}?force=`, `DELETE /api/projects/{id}/lora/{filename}`. **Append-only
> is still the rule** — these are deliberate, guarded exceptions, never automatic, and nothing
> else in the codebase may delete a version. Guards to preserve if you touch this code: current
> version and last-remaining version are undeletable; a signed-off baseline needs `force=true`;
> version children are **re-parented onto the deleted version's parent** (never orphaned) and
> `images.version_id` is nulled rather than cascading; a project with a `running` job returns
> 409; clones are orphaned (`parent_project_id = NULL`), never cascade-deleted; build-folder
> removal requires the folder be a direct child of `BUILDS_ROOT`; LoRA filenames must be bare
> path components. Deleting the selected `pose_lora` clears the selection. Note when testing:
> a hand-inserted `running` job row gets flipped to `error` by the orphaned-job reaper, so stage
> the 409 case against a job the reaper won't touch.

> **2026-07-26 — v0.8.2: the emotion map (H1a).** `emotion_axes` + `emotion_tiers`, seeded
> from `DEFAULT_EMOTION_AXES` on first boot and **fully editable** (`/api/emotion-map` CRUD +
> reset; tier reorder is a swap endpoint that renumbers 1..N). 10 axes / 35 tiers = ST's 28
> regrouped + 7 custom top tiers. `EXPRESSIONS_28`, `presets()` and `_sprite_stem` are now all
> **derived from the map** — no hand-maintained lists. `poses.axis`/`poses.tier` added and
> backfilled by name, but **the map is authoritative**: `poses_list` re-resolves each pose
> against the current map by name, so an edited map re-groups the grid immediately.
> `_ST_BUILTIN_28` is a fixed external contract (what ST's classifier can emit), deliberately
> separate from the editable map; `custom` is derived as `not builtin`, never stored.

> **2026-07-26 — v0.8.0 starts Phase H (emotional depth).** Design lives in
> `docs/emotion-depth.md`; read it before touching LoRA/dataset/pose code. Shipped in 0.8.0:
> the **concept LoRA stack** (H1b) — `prompt_versions.lora_stack_json` + a `concept_loras`
> library table, `workflows.build_graph(..., lora_stack=)` splicing a chain of **core**
> `LoraLoader` nodes (anchor mode for `base-character-lora`, inject mode for `pose-with-lora`),
> applied to previews, dataset batches and pose renders, with trigger words auto-appended.
> Still to build in H1: axes×tiers map, dataset layers + `mode="emotion"` enrichment behind the
> **baseline gate** (no focused build before the basic 28 exist and are reviewed), `lora_builds`
> versioning, and per-axis selective sprite rebuild. **Roadmap resequenced**: Character Studio
> moved 0.8→0.9.

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
| `workflows/base-character-lora.json` + `.manifest.json` | Studio variant with a full `LoraLoader` (model+CLIP) for an external style LoRA; auto-selected when a version has `style_lora` set (0.7.9) |
| `docker/docker-compose.yml` | The stack: persona-forge + docker-socket-proxy + networks |
| `docker/.env` | Tracked, non-secret config (the only `.env` git tracks) |
| `PROJECT_PLAN.md` | Master spec + phased roadmap (this repo's "project brief") |
| `docs/ui-style.md` | UI design tokens from the user's esp32-shutter-hub card |
| `docs/emotion-depth.md` | Phase H design: emotion axes×tiers, dataset layers + per-emotion LoRA enrichment, selective sprite rebuild, and the post-1.0 ST emotion state engine |
| `CHANGELOG.md` / `RELEASE_NOTES.md` | Keep-a-Changelog / current release body (rewritten each release) |

## API surface

- **Health/status:** `GET /api/health`, `/api/comfyui/status`, `/api/storage/status`.
- **Models/workflows:** `GET /api/models?kind=` (returns `default`), `/api/workflows[/{id}]`.
- **Concept LoRA library (0.8.0):** `GET/POST /api/concept-loras`, `PATCH/DELETE
  /api/concept-loras/{id}`. Global (not per project). List annotates each row with
  `available` (whether ComfyUI still sees the file). The per-version *stack* is not here —
  it rides on the prompt version as `lora_stack_json`.
- **Emotion map (0.8.2):** `GET /api/emotion-map`; `POST/PATCH/DELETE
  /api/emotion-map/axes[/{id}]`; `POST/PATCH/DELETE /api/emotion-map/tiers[/{id}]`;
  `POST /api/emotion-map/tiers/{id}/move?direction=up|down`; `POST /api/emotion-map/reset`.
  Every mutation returns the whole map, so the UI re-renders from the server's answer.
  `GET /api/projects/{id}/poses` now also returns `axes` (per-axis done/total) for grouping.
- **AI assistant:** `GET /api/ai/status` (reachable/loaded), `POST /api/ai/warm`,
  `/api/ai/unload`, `/api/ai/suggest-prompt` (`{instruction, mode: replace|modify, character, style, negative}`).
- **Container control:** `GET /api/containers/status`, `POST /api/containers/{key}/start`,
  `/api/containers/{key}/restart?force=` (`key` ∈ `comfyui`, `ollama`).
- **Projects/versions:** `POST/GET /api/projects[/{id}]`, `.../versions`, `.../signoff`,
  `.../rollback/{version_id}`, `.../clone`, `.../generate`, `.../repair-permissions`.
- **Admin / deletion (0.8.3):** `DELETE /api/projects/{id}?delete_files=`,
  `DELETE /api/versions/{id}?force=`, `DELETE /api/projects/{id}/lora/{filename}` — see the
  0.8.3 banner at the top for the guards each one enforces.
- **Images/builds:** `GET /api/image` (0.8.6: disk-first off `/builds`, ComfyUI proxy only as
  fallback), `/api/builds`. **Logs:** `GET /api/logs[/persisted]`.

## Data model (SQLite, append-only versioning)

- `projects` (name, slug, `current_version_id`, `parent_project_id` for clones).
- `prompt_versions` — **append-only**; a row is never edited, and the only thing that removes
  one is the explicit admin delete added in 0.8.3 (guarded; see the banner). Fields:
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
- **0.7.4:** Phase 7 — **Stop a running build.** "Stop build" button on the LoRA-tab Build panel
  (shown while queued/running). Cancelling a running `lora_build` now also **interrupts ComfyUI**
  (`comfy.interrupt()` + `comfy.clear_pending()` in `POST /api/jobs/{id}/cancel`) so the GPU frees
  immediately, not just the pipeline. Cooperative job-cancel alone left the in-flight training
  churning the GPU.
- **0.7.5:** Phase 7 — **delete dataset candidates.** "Purge unselected" button (removes all
  `selected=0` dataset images — DB rows + files on `/builds`; `POST .../dataset/purge`) + a
  per-thumbnail 🗑 delete (`DELETE .../dataset/{image_id}`). File delete via `_delete_dataset_file`
  (guards against escaping `BUILDS_ROOT`, best-effort). Both confirm + irreversible.
- **0.7.6:** Phase 7 — **targeted dataset generation + bigger variety sets.** `mode` field
  (`both`|`faces`|`poses`) on `POST .../dataset/generate` + a Dataset-tab select, so a batch can
  reinforce a weak axis. Framings split into `DATASET_FACE_FRAMINGS` (9 head angles) and
  `DATASET_BODY_FRAMINGS` (24 poses/views around the body); `DATASET_EXPRESSIONS` grew to 18;
  `DATASET_POSE_EXPRESSIONS` (light) used on full-body shots. `_dataset_variation(n, mode)`:
  faces = face×expr, poses = body×light-expr, both = alternate face/body (~50/50).
- **0.7.7:** Phase 7 — **fix: a stopped/failed build stranded `projects.train_status='training'`**
  (no prompt id → reconciler couldn't clear it), so every future build died with "a training run
  is already in progress." `_reconcile_training` now auto-heals an orphaned flag from ComfyUI
  reality (null `train_prompt_id` = always stale; vanished prompt = stale once `queue_size()==0`)
  and runs before the gate in `_start_lora_training`; `cancel_job` also resets the flag on stop.
- **0.7.8:** Phase 7 — **LoRA build dates.** `_lora_files(slug)` returns each `.safetensors`
  `{name, modified, modified_ts, size}` **newest first** (file mtime = the reliable "built on"
  signal, bumps on rebuild); used by `GET .../lora` and `.../pose-config`. LoRA tab shows date +
  "latest" tag; Poses dropdown + selected-hint show it too — so a refresh is verifiable. Possible
  follow-up: an **embedded build stamp** (sidecar JSON or safetensors `__metadata__`: version,
  steps, dataset count).
- **0.7.9:** Phase 7 — **style LoRAs in the Prompt Studio.** New `workflows/base-character-lora.json`
  (`CheckpointLoaderSimple → LoraLoader (model+CLIP) → KSampler`, CLIP encoders read the LoRA's clip
  too). `_resolve_style_lora(base, name, strength)` upgrades `base-character` → `base-character-lora`
  when a version has `style_lora` set (one strength → both model+clip); no-op otherwise. Used by
  both `generate()` (optional `style_lora`/`style_lora_strength` on the request) **and**
  `dataset_generate()` (so the style reaches the trained character LoRA). Persisted per version:
  `prompt_versions.style_lora`/`style_lora_strength` (auto-migrated). Studio gets a *Style LoRA*
  dropdown (`/api/models?kind=loras`) + strength slider under Checkpoint. Distinct from the Poses
  tab's model-only *character* LoRA. **Not yet verified against a live ComfyUI run.**
- **0.7.10:** Phase 7 — **A1111-style sampler controls in the Prompt Studio.** New collapsible
  "Generation settings" block (`<details class="gen-settings">` in `index.html`) exposing Steps /
  CFG / Sampler / Scheduler, defaults 28 / 5.0 / `euler_ancestral` / `normal`. **Frontend-only** —
  the `base-character`/`base-character-lora` manifests + `/generate` already accepted these params;
  `genSettings()` in `app.js` reads the four fields and `Object.assign`s them into the generate
  params. **Ephemeral by design:** kept out of `formValues()`, so they never touch the saved
  version / diff / rollback (no schema change). Not yet verified against a live ComfyUI run. Sampler/
  scheduler `<option>`s are a curated static list (canonical ComfyUI enum names), not fetched from
  `/object_info`. Possible follow-up: **persist per version** (steps/cfg/sampler/scheduler columns,
  the fuller "reproducible baseline" option we deferred).
- **0.8.0:** **Phase H1b — concept LoRA stack.** Separates the *character* LoRA (identity, one,
  trained here) from *concept* LoRAs (pose/gesture/expression, third-party, stacked). New
  `concept_loras` library table + `prompt_versions.lora_stack_json` (JSON on the version, **not** a
  child table — versions are append-only, so the stack versions and rolls back for free; entries
  store the **filename**, not a library id, so curating the library can't break a saved version).
  `workflows.apply_lora_stack()` splices a chain of **core `LoraLoader`** nodes and repoints
  downstream consumers at the tail; two manifest shapes — `lora_chain: {node}` (**anchor**, reuses
  `base-character-lora`'s existing loader) and `lora_chain: {class_type, model_source, clip_source,
  id_prefix}` (**inject**, builds the whole chain for `pose-with-lora`, whose character LoRA is
  `LoraLoaderModelOnly` with CLIP off the checkpoint — avoids a second workflow file and leaves the
  graph untouched when the stack is empty). `_resolve_style_lora()` now returns
  `(workflow, params, chain)` with the style LoRA as chain entry 0. Applied to **previews, dataset
  batches and pose renders**; `_stack_triggers()`/`_apply_stack_triggers()` append de-duplicated
  trigger words to `style`; `_check_stack_files()` fails fast on a missing file. **Verified against
  live ComfyUI 0.28.0** (graphs, CRUD, versioning/rollback, guard, browser interactions); no image
  rendered as part of that. Note `logs.info(category=...)` collides with the logger's own first
  param — pass `kind=` instead.
- **0.8.1:** base-model neutrality — the concept-LoRA work no longer reads as if one checkpoint
  family is the target (wording only; `base_model` was always free text per entry).
- **0.8.2:** **Phase H1a — the emotion map.** See the header note. Key design calls: the map is
  DB-backed and editable (the shipped table is a *default*, not a vocabulary); `graded` separates
  real intensity ladders from mere groupings; the map is authoritative over `poses.axis/tier`;
  tier labels are UNIQUE because they are sprite filenames; reorder is a swap+renumber, not a
  raw position write (which would create ties broken by row id).
- **0.8.3:** **Admin tools** — the first deletion capability in the app (persona / prompt version
  / trained LoRA). See the header note for the guards. Key design calls: destroying the build
  folder is a *separate* explicit choice from removing the DB record (an hour of GPU time is not
  the same decision as a row); version children are re-parented rather than orphaned so history
  stays a connected chain; clones are orphaned rather than cascade-deleted; a running build
  refuses (409) rather than racing the worker. Browser-verified end to end.
- **0.8.4:** **Phase H3a — structural pose control.** See the header note. Key design calls:
  ControlNet is spliced into any workflow declaring a `controlnet` manifest block rather than
  shipping a parallel template family (conditioning vs model/CLIP makes it orthogonal to the LoRA
  chain); the face pass is a **separate graph over the stored pass-1 image**, which is what makes
  an expression re-roll cheap and non-destructive; per-pose overrides are nullable so the persona
  dial stays authoritative; per-pose seeds are **derived** from the version seed, not random, so a
  set is still reproducible. The defaults are measured against the live box, not chosen — see the
  `docs/pose-control.md` §4.0 calibration before changing them.
- **0.8.5:** **Phase H3b — the pose library.** See the header note. Key design calls: keypoints
  are the source of truth and the PNG is derived (resolution-independent, editable, and the
  precondition for the stickman editor); the library is global because a skeleton is
  character-agnostic; deleting an entry unlinks poses rather than invalidating renders, since the
  staged skeleton lives in ComfyUI's input.
- **Remaining:** **H3c** (ControlNet in the dataset build — now the priority, see the measured
  LoRA-vs-skeleton finding), **H3g** (Blender as a skeleton source: read bone positions and
  project to COCO-18 — no render, so headless is fine; see `docs/pose-control.md` §6.2), and
  (ControlNet in the dataset build, which is what teaches the LoRA the body at all); rest of
  **Phase H1** (dataset layers + emotion enrichment behind the
  baseline gate, `lora_builds` versioning, per-axis selective rebuild — see `docs/emotion-depth.md`)
  · Character Studio at **0.9** · 1.0 release. The **dataset side of the weak-LoRA fix is now
  complete** (0.7.2 pose + 0.7.3 framing/expression + 0.7.6 targeting/variety).
- **Future infra (HARDWARE-GATED, added 2026-07-26):** user is swapping UR1's RTX 3060 → a second
  **RTX 3090 24 GB** (→ 2× 3090). Unlocks a **dedicated training GPU**: a 2nd ComfyUI instance
  pinned to GPU 2 (reuse `lora-train.json`) as the trainer via a PF `TRAIN_COMFYUI_URL`, main
  ComfyUI stays free for generation → **true parallel builds + generation** (the real fix for
  background/concurrent builds + gen-while-training). Job engine can then relax serial→`lane`s.
  Alternatives: kohya trainer, RunPod burst. Not viable today (one training-class GPU; 3060 too
  small). Full spec: `PROJECT_PLAN.md` §7 "Future infrastructure — dedicated training GPU". 0.7.x backlog (priority order):
  **(1) raise the automated training-step default to ~1500-2500** (last recipe lever);
  **(2) custom / editable dataset example prompts** (user request 2026-07-25) — per-project
  user-added example shots on top of the `DATASET_FRAMINGS × DATASET_EXPRESSIONS` auto-rotation
  (each a modifier via the `expression` suffix, optional per-entry count), plus surfacing the
  built-in lists as editable defaults; reconciles into `images(kind='dataset')`, no trainer
  change (see `PROJECT_PLAN.md` §7). Optional polish: reuse-parent-LoRA for clones
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
