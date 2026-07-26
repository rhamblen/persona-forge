# Changelog

Versioning is `0.<phase>.<iteration>` — the middle digit is the project phase, the
last increments with each update inside that phase. `1.0.0` will be the first
complete release.

Every version below is a **published GitHub Release** with a matching
`ghcr.io/rhamblen/persona-forge` image tag. Nothing is parked under "Unreleased".

---

## [0.8.0] — 2026-07-26

**Concept LoRA stack — overlay pose/gesture LoRAs on any render.** (Phase H, stage 1b —
the first piece of the emotional-depth work in [`docs/emotion-depth.md`](docs/emotion-depth.md).)

Separates the two kinds of LoRA this pipeline uses: the **character LoRA** (trained here, one
per persona, carries *who*) and **concept LoRAs** (third-party, stacked, carry *what the body is
doing* — arm movement, sitting positions, gestures). Reach poses the character LoRA can't produce
alone, and — the reason this lands first — generate enrichment data that adds **external signal**
instead of recycling the model's own output.

### Added
- **Concept LoRA stack** in the Prompt Studio — a collapsible panel to stack N LoRAs on top of the
  style/character LoRA: enable/disable, per-entry strength, reorder (the chain applies top-down),
  and remove. **Saved on the prompt version**, so it rolls back with everything else and shows up
  in the version diff as `lora_stack`.
- **Concept LoRA library** (`/api/concept-loras`, CRUD + a "Manage library…" modal) — register a
  LoRA once and stack it on any persona. Each entry records **base-model compatibility** (an SD1.5
  LoRA will not load on an SDXL checkpoint), **trigger words**, a recommended weight range, and a
  category (`pose` / `gesture` / `expression` / `style`).
- **Trigger words are appended to the prompt automatically** for enabled entries, de-duplicated
  case-insensitively. Most concept LoRAs are inert without them, so the stack has to reach the
  prompt as well as the model graph.
- **Pre-flight check**: a stacked LoRA ComfyUI can no longer see fails with a message naming the
  file, instead of a node-level ComfyUI error that doesn't say which entry broke.

### Changed
- `workflows.build_graph()` takes an optional `lora_stack` and splices a **chain of core
  `LoraLoader` nodes**, threading model+CLIP through in order and repointing every downstream
  consumer at the chain tail. Two manifest shapes: **anchor** (`base-character-lora` reuses its
  existing loader) and **inject** (`pose-with-lora` gets a full model+CLIP chain built from
  nothing, since its own character LoRA is model-only with CLIP straight off the checkpoint).
- The stack applies to **all three generation paths** — Studio preview, dataset batches, and pose
  renders. Dataset batches matter most: a pose/gesture LoRA is exactly how a dataset gains
  body-language variety the checkpoint won't produce on its own.
- `_resolve_style_lora()` now returns the workflow, the single-LoRA params, and the full chain;
  the style LoRA is always chain entry 0, so identity is applied before the overlays on top of it.

### Notes
- **Core ComfyUI nodes only.** A "power LoRA loader" custom node would be tidier, but
  `custom_nodes` is read-only over SMB on UR1 — this needs nothing installed.
- **Migration is automatic**: `prompt_versions.lora_stack_json` (default `[]`) and a new
  `concept_loras` table are added on boot. Existing versions are untouched and render exactly as
  before. The stack is stored as JSON *on the version* rather than in a child table precisely
  because versions are append-only — it then versions and rolls back for free.
- Stacks store **filenames, not library ids**, so deleting a library entry can never break a saved
  version; the stack row just shows as `unregistered` and can be removed.
- Keep stacks to **2–3**: stacked LoRAs fight, and identity is what loses. Entries default to the
  middle of the library's recommended weight range rather than 1.0.
- **Verified** against a live ComfyUI (0.28.0) for graph construction, library CRUD, version
  save/inherit/rollback, the missing-file guard, and every stack interaction in the browser. No
  image was generated as part of this verification.

## [0.7.10] — 2026-07-26

**A1111-style sampler controls in the Prompt Studio.** (Phase 7.)

### Added
- **"Generation settings" panel under the Studio form** — a collapsible block exposing
  **Steps**, **CFG**, **Sampler**, and **Scheduler**, so you can tune the preview render the way
  Automatic1111's *Steps* / *CFG* / *Sampling method* controls let you. Defaults match the
  workflow (28 / 5.0 / `euler_ancestral` / `normal`), so leaving it collapsed changes nothing.
- Inline tip that `euler_ancestral` peaks around 28 steps, and to pair `dpmpp_2m` with `karras`
  for sharper detail from higher step counts.

### Notes
- **Frontend-only change.** The `base-character` / `base-character-lora` manifests and the
  `/generate` endpoint already accepted these params — this just surfaces controls for them.
- **Ephemeral by design.** The four settings affect the **current preview run only**; they are
  deliberately kept out of the saved version (no schema change, not in the version diff). Persisting
  them per version remains a possible future step.
- Not yet verified against a live ComfyUI run.

---

## [0.7.9] — 2026-07-26

**Use an external style/detail LoRA in the Prompt Studio.** (Phase 7.)

### Added
- **Style LoRA picker in the Prompt Studio.** A *Style LoRA* dropdown (from ComfyUI's `loras`
  folder) plus a strength slider under the Checkpoint field. Selecting one makes Generate render
  the checkpoint **with that LoRA loaded**; *none* renders checkpoint-only exactly as before.
- The selection is **saved per version** (`style_lora` + `style_lora_strength`) — part of the
  append-only history, surfaced in the version diff, and restored on rollback.
- **Dataset generation applies it too**, so the style carries into the trained character LoRA
  instead of being lost when the dataset is built.

### Changed
- New `base-character-lora` workflow: loads the LoRA via a **full `LoraLoader`** (model + CLIP, so
  text-encoder-side style LoRAs work). Studio and dataset generation upgrade `base-character` →
  `base-character-lora` automatically when a LoRA is set; behaviour is identical when none is.
  Distinct from the Poses tab's model-only *character* LoRA path, which is unchanged.
- `POST /api/projects/{id}/generate` accepts optional `style_lora` / `style_lora_strength`.
- `prompt_versions` gains `style_lora` (TEXT) + `style_lora_strength` (REAL); older DBs migrate
  automatically on boot (existing versions default to no LoRA).

---

## [0.7.8] — 2026-07-26

**Show each LoRA's build date — so you know a rebuild actually took.** (Phase 7.)

### Added
- **Build date on every trained LoRA.** The LoRA tab now lists each `.safetensors` with its
  build time (the file's modified time — it bumps on every rebuild), **newest first**, and tags
  the most recent one **latest**. So after a refresh/retrain you can confirm at a glance that
  you're on the fresh version.
- The **Poses** Character-LoRA dropdown shows each LoRA's date in the option, and the selected-
  LoRA hint reads "…(built <date>)", so you can verify the pose set is using the refreshed LoRA.

### Changed
- `GET /api/projects/{id}/lora` and `.../pose-config` now return each LoRA as
  `{name, modified, modified_ts, size, …}` (newest first) instead of a bare name.

---

## [0.7.7] — 2026-07-26

**Fix: a stopped build could jam all future builds with "a training run is already in progress."**
(Phase 7.)

### Fixed
- **Stopping (or a failure of) a build no longer strands the project in `training` forever.**
  A `lora_build` cancelled during the training stage left `projects.train_status = 'training'`
  with no ComfyUI prompt behind it. The reconciler needs a prompt id to clear the flag, so it
  stayed stuck — and every subsequent build died immediately with *"a training run is already in
  progress for this persona."* Two-part fix:
  1. **Auto-heal.** `_reconcile_training` now clears an orphaned `training` flag judged from
     ComfyUI reality (a real run always has a `train_prompt_id`, so a null id is stale; a
     vanished prompt is stale once ComfyUI's queue is idle). It also runs right before the
     "already training" gate, so a rebuild self-heals — no need to open the LoRA tab first.
  2. **Stop resets the flag.** Cancelling a running build now sets `train_status` off `training`
     as part of the stop, so it can't get stuck in the first place.

---

## [0.7.6] — 2026-07-25

**Target the dataset at a weak axis, and a lot more variety.** (Phase 7.)

### Added
- **Variety mode selector** on the Dataset tab — pick what a batch spreads across so you can
  top up whichever axis looks weak:
  - **Both** (default) — alternates close-up faces and full-body poses (~50/50).
  - **Faces** — close-up/bust framings at varied head angles × the full expression range.
    Use this when the face is weak or you want more expressions.
  - **Poses & views** — full body from many angles and actions. Use this when the LoRA can't
    hold poses or you want more coverage around the body.
  - **Off** — same framing, seed only (the old plain behaviour).
  `mode` field on `POST /api/projects/{id}/dataset/generate` (`both` | `faces` | `poses`).

### Changed
- **Much larger variety sets.** Framings are now split into a **face** pool (9: front, 3/4 L/R,
  profile, up, down, over-the-shoulder, bust) and a **body** pool (**24**: front/back/left/right,
  3/4 front & back, low/high angle, walking, walking-away, running, sitting on floor/chair,
  kneeling, crouching, leaning, arms crossed, hands on hips, arms raised, jumping, cowboy shots,
  twisting, waving). Expressions grew from 10 to **18** (added laughing, warm smile, crying,
  annoyed, surprised, nervous, thoughtful, pouting, confident smirk). Full-body shots use a
  light expression set (the face is tiny there) so the emphasis stays on the pose/view.

---

## [0.7.5] — 2026-07-25

**Delete dataset candidates — purge the unselected, or drop one.** (Phase 7.)

### Added
- **"Purge unselected" button** on the Dataset tab. After you've cherry-picked the keepers,
  one click removes **every unselected candidate** — both the DB rows and the image files on
  `/builds` — leaving just your selected training set. The button shows the live count
  ("Purge 12 unselected") and hides when nothing is unselected. `POST
  /api/projects/{id}/dataset/purge`.
- **Per-candidate delete.** Each dataset thumbnail now has a 🗑 badge (on hover) that deletes
  that one image (DB row + file), selected or not. `DELETE /api/projects/{id}/dataset/{image_id}`.

### Notes
- Both actions confirm first and are **irreversible** (the files are unlinked from `/builds`).
- File deletion is guarded against escaping the builds root and is best-effort — a candidate is
  always removed from the dataset even if its file was already gone.

---

## [0.7.4] — 2026-07-25

**Stop a running build from the UI — and actually free the GPU.** (Phase 7.)

### Added
- **"Stop build" button** on the Build panel (LoRA tab), shown whenever a build is queued or
  running. One click (with a confirm) cancels the build. Previously the only way to stop a
  build was to POST the cancel endpoint by hand.

### Changed
- **Cancelling a running `lora_build` now interrupts ComfyUI too.** The job cancel is
  cooperative — it stops the *pipeline* from advancing, but the training run already handed to
  ComfyUI would keep using the GPU until it finished. The cancel endpoint now also calls
  ComfyUI `POST /interrupt` and clears its pending queue, so the **GPU is freed immediately**.
  New `comfy.interrupt()` / `comfy.clear_pending()`; wired into `POST /api/jobs/{id}/cancel`.
  Best-effort — if ComfyUI is unreachable the job is still flagged and the worker finalizes it.

---

## [0.7.3] — 2026-07-25

**Close-up + full-body framings and varied expressions in the dataset — the other half of the
weak-LoRA fix.** (Phase 7.)

### Changed
- **Dataset variety is now two axes: framing *and* facial expression.** 0.7.2 varied pose;
  this adds the two things that were still making trained LoRAs weak:
  - **Framing distance.** ~40% of a batch is now **close-up / bust** framing (face fills the
    frame) with the rest full-body/pose. Identity fidelity comes from *face pixels* — an
    all-full-body set gives a tiny, blurry face and a weak LoRA. Close-ups fix the face; the
    wider shots still teach body, outfit and pose independence.
  - **Facial expression.** Candidates now cycle through neutral, happy, sad, angry, shocked,
    embarrassed, alluring and flirtatious (neutral-weighted). A single baked-in expression
    otherwise glues itself into identity and fights expression prompts later (the same
    "smile leaks into grief" failure the pipeline already knew about). Because the trainer
    captions every image (Florence-2), the expression lands in the caption and **decouples**
    from the trigger word instead of binding to it.
- The two axes rotate independently (12 framings × 10 expressions), so a batch of 30 yields
  **30 unique framing+expression pairs** with none repeated, and *+10 more* continues the
  rotation. Still injected through the base-character `expression` suffix — no new graph, no
  schema change.
- The Dataset-tab toggle is relabelled **"Framing, pose & expression variety."**

### Caveat
- If a project's **style** prompt hard-codes a framing (e.g. "full body"), it can fight the
  close-up candidates — the suffix usually wins but not always. The app does **not** rewrite
  your prose to resolve this; drop framing words from the style field if close-ups come out wide.

---

## [0.7.2] — 2026-07-25

**Pose & framing variety in the Dataset Builder — the fix for weak, pose-locked LoRAs.** (Phase 7.)

### Changed
- **Dataset batches now spread candidates across a range of poses and framings** instead of
  varying only the seed. Every candidate is drawn from a different framing/pose (full body,
  sitting, walking, arms crossed, three-quarter, side profile, low angle, back view, portrait…)
  *and* a fresh seed. This is the highest-leverage quality fix in the pipeline: a training set
  built this way teaches the LoRA identity **independent of stance**, so the trained character
  can actually do the starter poses instead of collapsing to the one waist-up stance it was
  trained on (the `sweetie-pie` failure). The pose is injected through the base-character
  `expression` suffix — the same, already-validated lever the Poses tab uses — so no new
  workflow or graph change was needed.
- **The rotation continues across batches.** *Generate 30* then *+10 more* keeps cycling from
  where the last batch left off, so pose coverage stays even rather than restarting at pose 0.

### Added
- **"Pose & framing variety" toggle** on the Dataset tab (on by default). Uncheck it for a
  same-pose, seed-only batch — the original behaviour — when you deliberately want one stance.
  New `pose_variety` field on `POST /api/projects/{id}/dataset/generate` (defaults to `true`).

---

## [0.7.1] — 2026-07-25

**Prompt Studio fixes.** (Phase 7.)

### Fixed
- **New personas start with default negatives instead of a blank field.** A fresh project
  used to store an empty negative prompt, so the Prompt Studio negatives field came up blank.
  New projects now seed the canonical starter negative (read from the `base-character` template
  — one source of truth, exposed at `GET /api/prompt-defaults`), pre-filled in the field and
  fully editable. Existing projects with an empty negative also show the default.
- **Version numbers are now per-persona (v1, v2, …), not the global counter.** The version rail
  and current-version chip showed the global `prompt_versions` row id, so a new character's
  first version could read "v37". The UI now numbers each project's versions from 1 by creation
  order; API calls still use the real id under the hood.

---

## [0.7.0] — 2026-07-25

**Unattended builds: a background job engine.** (Phase 7 — orchestration.)

### Added
- **Generic background job engine** (`jobs.py` + `jobs` table). A single in-process asyncio
  worker drains a persisted FIFO, advancing the one running job stage-by-stage until it
  finishes — so a build runs **unattended with the browser closed**. It's kind-agnostic:
  handlers register per `kind`, so the lorebook generator, cast/campaign builder, and source
  ingestion (PROJECT_PLAN Phase E/F/G) plug in later with **zero engine changes**. Jobs are
  **resume-safe** — all progress lives in the row (`stage` + `state_json`), so a container
  restart re-reconciles the running job instead of losing it. Serial by design (the GPU is
  serial); a future `lane` can run non-GPU jobs alongside.
- **First handler — `lora_build`:** one click runs **train LoRA → auto-apply it → render the
  first-draft 28 expressions**. If ComfyUI can't see the freshly trained LoRA, the build
  **restarts ComfyUI** (via the existing scoped docker-socket proxy) to bind it, then renders;
  if it still can't bind (or container control is off), it **degrades to base-character poses**
  and says so in the result rather than failing. Reconciles training + poses from ComfyUI
  history each tick.
- **Job endpoints:** `POST /api/projects/{id}/jobs` (enqueue; rejects a duplicate active build
  and an unstaged dataset), `GET /api/projects/{id}/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`,
  `POST /api/jobs/{id}/cancel`.
- **"Build overnight" panel** on the LoRA tab — steps / rank / strength + a live stage/progress
  bar. Kick it off and close the tab; the server finishes the build.

### Notes
- The manual **Train** and **Generate all** buttons still work standalone — the job engine
  reuses the same training/pose code (factored into shared helpers), it doesn't replace them.
- Deferred to Phase F (per the plan): the multi-character **add-to-queue** cast builder, and
  concurrency lanes — both ride this same engine.

---

## [0.6.2] — 2026-07-25

**LoRA-driven poses, a training timer, and clearer export wording.** (Phase 6 — the joint
LoRA-into-poses deliverable.)

### Added
- **Pose renders can now load the trained character LoRA.** New `workflows/pose-with-lora.json`
  (+ manifest): `CheckpointLoaderSimple → LoraLoaderModelOnly → KSampler`, with the project's
  **trigger word prepended** to the positive prompt. The Poses tab gained a **Character LoRA**
  selector (with strength) — pick a trained LoRA per project and pose renders stay on-model;
  leave it on *None* and poses render from the base character as before. Endpoints
  `GET /api/projects/{id}/pose-config` and `POST /api/projects/{id}/pose-lora`; new
  `pose_lora` / `pose_lora_strength` columns on `projects`.
  - The selector flags LoRAs that exist on disk but aren't visible to ComfyUI yet, with a hint
    to add `persona_forge: { loras: /builds }` to ComfyUI's `extra_model_paths.yaml` and
    restart — the one manual prerequisite for trained LoRAs to appear in ComfyUI's list.
- **Training timer + ETA.** The LoRA tab now records a **start time**, and on completion logs
  the **wall-clock duration** (and s/step) at `info` level — so past run-times are searchable
  in the log for future reference. While a run is in progress the tab shows **elapsed time and
  an ETA** derived from the previous run's duration. New `train_started_at` / `train_steps` /
  `last_train_seconds` / `last_train_steps` columns on `projects`.

### Changed
- **Export panel relabelled "Export to builds folder"** (was "Export to SillyTavern"). It has
  always staged sprites into the project's build folder for you to copy into SillyTavern
  manually; the wording now says so plainly. No behaviour change.

### Notes
- Training on UR1 shares one RTX 3090 with other GPU containers (ollama, chatterbox-st,
  immich, a-eye). If those hold VRAM, `TrainLoraNode` can OOM even though ComfyUI frees its own
  memory first. Stop the aux GPU containers (or let Ollama evict) before a training run until
  the aux services are moved to the idle RTX 3060.

---

## [0.6.1] — 2026-07-25

**Export the pose set to SillyTavern sprites.** (Phase 6.)

### Added
- **Export to SillyTavern** on the Poses tab. Every rendered pose is matted to a
  **transparent PNG** (BEN2) and named for SillyTavern — an exact expression name (e.g.
  `joy`) is kept verbatim so ST recognises it, anything else is slugified, and name
  collisions are de-duped. Sprites land in `<build>/export/<Character>/` and are **staged
  only — never auto-copied into SillyTavern** (the deliberate manual step from the project's
  settled decisions). Queued + reconciled from ComfyUI history like the rest.
- New `workflows/bg-remove.json` (BEN2 `rem_mode` + WAS `Image Save` with
  `prefix_as_filename` for exact `<name>.png` output — the only matte path that works on this
  ComfyUI, per `workflows/README.md`). New `export_jobs` table; endpoints
  `GET/POST /api/projects/{id}/poses/export`.

---

## [0.5.3] — 2026-07-25

**Per-image auto-captioning for training.** (Phase 5.)

### Changed
- **Training now auto-captions each image with Florence-2** instead of using the trigger
  word alone. The training graph gained an inline caption stage — `Florence2ModelLoader` +
  `Florence2Run(task=caption)` per image → `StringConcatenate` prefixes your **trigger word**
  (`pf_<slug>, <caption>`) → per-image `CLIPTextEncode` → `TrainLoraNode`. This is the
  "trigger word + light caption" scheme chosen for the project: the LoRA still binds to the
  trigger, but per-image captions help it separate the character's identity from pose and
  background. **Validated end-to-end with real runs** (the per-image conditioning is matched
  to the image batch — confirmed against `TrainLoraNode`).
- No app/API change — the existing train endpoint already passes the trigger; only the
  workflow template (`workflows/lora-train.json` + manifest) changed. First training run
  loads Florence-2 (adds VRAM + a little time; VRAM is freed before training as before).

---

## [0.5.2] — 2026-07-25

**The LoRA actually trains now.** (Phase 5 — released after 0.6.0 because the LoRA and
Poses phases are being built in parallel; version tags interleave.)

### Added
- **Train a character LoRA** from the staged dataset, end-to-end in ComfyUI. New
  **Train** section on the LoRA tab (steps / rank / learning-rate, defaults 500 / 16 /
  5e-4) and a **Train LoRA** button; live status (`training…` / `done` / `failed`) that
  polls until the run finishes, then the `.safetensors` appears under **Trained LoRAs**.
- The training graph (`workflows/lora-train.json`) is the **native ComfyUI `TrainLoraNode`**
  pipeline — `LoadImageDataSetFromFolder` → `ImageListToImageBatch` → `VAEEncode` +
  `CLIPTextEncode(trigger)` → `TrainLoraNode` → `SaveLoRA`. **Validated with a real 16-step
  run** before shipping.
- **Frees VRAM before training** — unloads the Ollama model and calls ComfyUI `/free`.
  Without this, training OOMs when ComfyUI/Ollama already hold the 3090 (observed).
- Endpoint `POST /api/projects/{id}/lora/train`; `projects.train_prompt_id` /
  `train_status` columns (auto-migrated); `comfy.free_memory()`.

### Notes
- **Loading the trained LoRA:** `SaveLoRA` writes to the build folder (`<slug>/lora/`), which
  is ComfyUI's output dir — **not** its `models/loras`, so a trained LoRA won't appear in
  ComfyUI's loras dropdown until you add a `loras` path to `extra_model_paths.yaml` (see the
  note in the LoRA tab).
- Captions are the **trigger word only** for now (the validated minimal path); per-image
  Florence2 light captions are the next enhancement.

---

## [0.6.0] — 2026-07-24

**Phase 6 opens: the Pose / Expression studio.** (Built in parallel with the LoRA phase —
tags interleave with any further 0.5.x work.)

### Added
- **Poses tab.** Build a set of poses / expressions for a character:
  1. **All created** — add poses individually, or load a preset (**Starter set** of 8 body
     poses, or the **28 SillyTavern expressions**), then **Generate all**. Each renders from
     the project prompt + the pose's modifier; rendering is queued and the grid fills in as
     ComfyUI finishes (a `poses` table + reconcile-from-history, like the dataset).
  2. **Select → zoom** — click a pose to open its editor; the preview image (and a Zoom
     button) open it full-size in the lightbox.
  3. **Modify** — with a selected pose, edit its **modifier** by hand *or* ask the AI
     ("make her sit cross-legged" → Ollama revises just that fragment), then **Save &
     regenerate** that one pose. The editor is available whenever a pose is selected.
  - Endpoints under `/api/projects/{id}/poses` (list / add / preset / update / delete /
    ai / generate / generate-all); new `poses` table; `ollama.revise()` for single-field edits.

### Fixed
- Logs: widened the level column so `VERBOSE` no longer wraps.

---

## [0.5.1] — 2026-07-24

**Logging overhaul: a `verbose` level, and logs that actually cover the whole pipeline.**

### Added
- **`verbose` log level** below `debug` — the firehose: every cross-system handshake, each
  file copied between shares, each poll. Shown in the Logs tab (new VERBOSE chip + min-level
  option, purple) and on stdout. Default view stays at INFO+, so verbose is opt-in.
- **Pipeline instrumentation.** Logging now runs *through* the process, not just at boot,
  with level chosen per step:
  - `integration` (verbose) — the actual handshakes: `→ ComfyUI POST prompt/upload/history`,
    `← …`, Ollama request/response, history polls, with sizes and timings.
  - `process` (info) — milestones: batch queued, reconcile results, "staging N images
    /builds → ComfyUI input/…", "staged X/Y".
  - `local` (verbose) — the share copies: reading each dataset image off `/builds`, byte
    counts; `warn` when a selected image is missing on the share.
  - `warn`/`error` — a dataset image that failed to render, an upload ComfyUI rejected, a
    file missing.
  - `api` (verbose) — every inbound request (`method path → status`, ms).
  - **Boot** now also handshakes ComfyUI and Ollama and logs whether each is reachable.

---

## [0.5.0] — 2026-07-24

**Phase 5 opens: the LoRA trainer (foundation).**

Training will run as a **ComfyUI workflow** (`TrainLoraNode` + Florence2 captioning) — no
separate trainer container. This release lays the groundwork; captioning and the training
run follow in 0.5.1 / 0.5.2.

### Added
- **LoRA tab.** Shows the selected-image count, an editable **trigger word** (the token the
  trained LoRA binds to — defaults to `pf_<slug>`), the staged status, and any trained LoRAs
  in `{slug}/lora/`.
- **Dataset staging.** "Stage dataset to ComfyUI" uploads the selected images into ComfyUI's
  `input/pf-<slug>` folder via its HTTP `/upload/image` API — so the native dataset loader
  can read them **with no extra mount** (ComfyUI's input dir isn't on the shared `/builds`
  volume). Endpoints: `GET/POST /api/projects/{id}/lora`, `.../lora/trigger`, `.../lora/stage`.
- New `projects.trigger_word` column (auto-migrated); `comfy.upload_image()`.

---

## [0.4.1] — 2026-07-24

### Added
- **Zoom on dataset candidates.** Each thumbnail now has a hover **⤢** badge that opens the
  full image in the lightbox (backdrop or Esc to close), so you can examine a snap closely
  before deciding. The zoom badge is separate from the click-to-select body — zooming never
  changes your selection.

---

## [0.4.0] — 2026-07-24

**Phase 4 opens: the Dataset builder.**

### Added
- **Dataset tab.** From the current prompt, generate a batch of candidate images
  (**Generate 30** / **+10 more**), each at a fresh random seed, then pick the ones that
  look like the *same person* in a selectable thumbnail grid. Selected images are the
  character's training set for the LoRA phase.
  - Generation is **queued, not blocking** — the batch is submitted to ComfyUI and the
    tab fills in as each image finishes (a reconcile step pulls completed prompts out of
    ComfyUI history into the `images` table as `kind='dataset'`). Survives a restart via a
    `dataset_jobs` table, so an in-flight batch isn't lost.
  - A **target N** (default 20, per-project) with a progress bar toward it, and live
    "generating… N left in queue" state while the batch runs.
  - Endpoints: `POST /api/projects/{id}/dataset/generate`, `GET …/dataset`,
    `POST …/dataset/select`, `POST …/dataset/target`. New `projects.dataset_target` column
    and `dataset_jobs` table (auto-migrated).

---

## [0.3.4] — 2026-07-24

**Modify stops over-editing.**

### Fixed
- **Modify was deleting text it wasn't asked to touch.** A small instruction like
  "make eyes green" could make the requested change but *also* drop whole unrelated
  sentences (background/aesthetic description, stray words) and rewrite the style and
  negative fields. The Modify instruction to Ollama is now strict: reproduce the current
  text **verbatim** and change only the specific words the instruction is about — no
  rephrasing, reordering, shortening, or dropping anything else; a field the instruction
  doesn't mention is returned exactly as given. Verified live: "make eyes green" now
  changes only the eye colour and leaves style/negative untouched. Anything the model
  still slips can be dismissed with the per-change ✕ from 0.3.3.

---

## [0.3.3] — 2026-07-24

**Reject AI changes one at a time.**

### Added
- **Per-change accept/reject in the AI diff.** Each change now carries its own **✕**
  button — reject a single change and only that span reverts to the previous text; the
  rest of the suggestion stays. A rejected change is shown dashed-outlined with its
  addition ghosted, and its button becomes **↺** to re-apply it, so every choice is
  reversible. "Reject all & undo" remains for the bulk case. Hand-editing a field retires
  that field's diff (so a later reject can't overwrite a manual edit) while other fields'
  diffs stay live. All client-side.

---

## [0.3.2] — 2026-07-24

**See what the AI changed.**

### Added
- **AI suggestions now show a word-level diff.** After Replace or Modify, a per-field
  diff appears under the AI box: **added/changed text highlighted green**, **removed text
  in red strikethrough**. Unchanged fields are omitted. This makes edits easy to scan and,
  more importantly, surfaces anything the model *dropped* — Modify is instructed to
  preserve untouched text and can never blank a whole field, but it is a local model, so
  the red diff is the real safeguard. The suggestion still lands in the editable fields
  with the existing reject-and-undo. Diff is client-side (LCS over whitespace tokens).

---

## [0.3.1] — 2026-07-24

**Log page reskinned to the house standard; stale-frontend caching fixed.**

### Changed
- **Logs tab now matches the esp32-shutter-hub web-UI log page** — a dark monospace
  terminal (`[time] LVL category: message`, level-coloured red/amber/blue/teal), a
  **Min level** dropdown plus **colour-coded level toggle-chips**, a **category chip row**
  (Persona Forge keeps its `boot`/`integration`/`process`/`local` filter), a buffered
  **count** pill and a **live** state indicator, **Auto-scroll**, **Clear**, and
  **Previous runs** (the persisted log file, incl. runs before this process). Filtering is
  client-side over the last 500 entries; each line carries its structured detail inline,
  dimmed. Replaces the previous grid-row/segment layout.

### Fixed
- **Browsers served a stale `app.js` after a deploy** — the new page loaded but old
  cached JS ran against it (the symptom: a new backend with a UI stuck on "checking…").
  The frontend is now served with `Cache-Control: no-cache`, so a browser always
  revalidates and picks up a new build on the next load. StaticFiles still sends
  ETag/Last-Modified, so an unchanged asset is a cheap 304. **After this build deploys, a
  hard refresh is no longer needed for future updates.**

---

## [0.3.0] — 2026-07-24

**Phase 3 opens: the AI prompt assistant.**

### Added
- **AI prompt assistant (Ollama).** A new card sits *above* the three manual prompt
  fields. Type a plain-language description or instruction, choose **Replace**
  (author all three fields fresh) or **Modify** (edit the current prompt), and hit
  **Suggest** — it fills character / style / negative in one shot. Nothing is saved
  automatically: the suggestion lands in the editable fields with a **reject-and-undo**
  link, and only becomes a version when you Save as usual.
  - Talks to Ollama over its native HTTP API (`/api/generate`, `format:json`), same
    "no JSON-RPC hop" reasoning as the ComfyUI client.
  - The system prompt enforces the project's settled rules: **prose, not Danbooru
    tags**, and **no expression/emotion/pose words in the character field** (a baked-in
    smile leaks into anger/grief). Verified live: "cheerful catgirl…" produced a clean
    identity with the mood kept out of `character`.
  - **Modify never destroys** a field the instruction didn't touch — if the model
    returns an empty field, the current value is kept.
  - Endpoints: `GET /api/ai/status` (reachability + model list, shown as a chip),
    `POST /api/ai/suggest-prompt`. Configurable via `OLLAMA_URL` / `OLLAMA_MODEL`
    (default `http://192.168.1.32:11434`, `llama3.1:latest`).
- **Ollama in the sidebar, with Connect / Unload.** The pinned connection block now
  shows Ollama alongside ComfyUI and Builds — `offline` / `idle` (reachable, model not
  in VRAM) / `loaded`. **Connect** preloads the model so the first suggestion is instant
  instead of a ~60s cold load; **Unload** frees VRAM immediately. Suggestions also carry
  a `keep_alive` (`OLLAMA_KEEP_ALIVE`, default 30m) so the model auto-unloads after a
  spell of no use rather than pinning VRAM on the shared, always-on box. Endpoints:
  `POST /api/ai/warm`, `POST /api/ai/unload`; status now reports `loaded`.
- **Start / restart ComfyUI and Ollama from the app.** Both run as containers on the
  same host (UR1) as Persona Forge, so the sidebar now offers **Start** (when a
  container is stopped) and **Restart** for each. A ComfyUI restart is refused while its
  queue is busy unless forced, so an in-flight generation isn't killed by accident.
  - Access goes through a **scoped `tecnativa/docker-socket-proxy` sidecar**, never the
    raw Docker socket. The proxy is limited to `CONTAINERS` (list/inspect),
    `ALLOW_START` and `ALLOW_RESTARTS` — it **cannot** create, remove or exec
    containers, nor touch images / volumes / networks. The real socket is mounted
    **read-only**, and the proxy sits on an `internal` network unreachable from the LAN.
  - Disabled by default-safe: unset `DOCKER_PROXY_URL` and the feature (and its UI)
    simply disappear. New endpoints: `GET /api/containers/status`,
    `POST /api/containers/{key}/start`, `POST /api/containers/{key}/restart?force=`.
  - Only recovers a *stopped container on a live host* — if UR1 is down, so is PF.
- **Preview zoom.** The generated preview is now click-to-zoom into a full-screen
  lightbox (click the backdrop or press Esc to collapse), plus an **open in new tab**
  link on the image caption.

### Fixed
- **New personas defaulted to a photoreal checkpoint.** The checkpoint dropdown was
  populated straight from ComfyUI, which returns models in folder order — so option
  0 was `!first/consistentFactor_euclidCinematicV61`, a cinematic photoreal model,
  and the first generate came out looking wrong for an anime persona. The default is
  now *resolved* rather than positional: exact match on `DEFAULT_CHECKPOINT`
  (`animi/NoobAI-XL-v1.1.safetensors`, the model the working 28-expression workflows
  use), else the first model matching `PREFERRED_CHECKPOINTS`
  (`NoobAI-XL`, `animi/`, `AnythingXL`), else position 0 with a warning logged.
  Both are env-overridable in `docker/.env`.
- `POST /api/projects` now resolves the default server-side when no checkpoint is
  given, so the initial prompt version records a real model instead of `''`.
- Projects created before this release stored an empty checkpoint and so inherited
  the same wrong option in the UI; the form now falls back to the resolved default,
  which fixes them without a db migration.

### Changed
- `GET /api/models` returns a `default` field alongside `models`.

---

## [0.2.7] — 2026-07-24

### Changed
- **`docker/.env` is now shipped as a real, tracked file** instead of
  `.env.example`. Copying `docker/` to the server now yields a working stack with
  no rename step. It contains only non-secret config (ComfyUI URL, host paths,
  port, `PUID`/`PGID`, `TZ`); `.gitignore` still ignores every other `.env`, with a
  single explicit exception for this one. If a credential is ever needed, it must
  move out of version control and the exception be dropped.

---

## [0.2.6] — 2026-07-24

**Consistency pass: docs, UI language, and self-describing builds.**

### Added
- **`persona.json` sidecar** written into each build folder (on create, clone,
  version change, sign-off and rollback). The sqlite db in `appdata/` remains the
  working store, but a build folder was not self-describing — copy it elsewhere or
  lose the db and you kept the images without the prompt that made them. Each build
  now carries its persona, current prompt, signed-off baselines and full version
  history.
- `docs/ui-style.md` now records the **actual design tokens** taken from the user's
  own `esp32-shutter-hub` HA card (10px radius, 1px borders, accent-ring selection,
  icon-above-label tiles, 11–12.5px muted secondary text, CSS-variable theming).

### Changed
- **Logs tab restyled for consistency** — the ad-hoc dropdown/checkbox toolbar is
  replaced with the shared `.seg-tile` idiom (accent border + inset ring for the
  selected state), matching the rest of the app and the shutter-hub card. Level
  badges are now chips; the row grid collapses on narrow screens.
- **README restructured** to match the conventions in `esp32-shutter-hub` and
  `pihole-mcp`: badges → intro → Why? → Features → Quick Start → Configuration →
  Repo layout → Status → Documentation → Related projects → License.
- **`## Status` no longer duplicates the changelog** — it is now a short statement
  of the current phase, pointing at `CHANGELOG.md` and `PROJECT_PLAN.md`.

### Fixed
- **Persistent state was created *inside* `docker/` instead of beside it, and was
  confusingly called `appdata`** (nested under `/mnt/user/appdata/` already). The
  compose file used a relative bind source, which docker compose resolves against
  its *project directory* — Unraid's Compose Manager does not reliably set that to
  the compose file's folder.

  Replaced with two explicit, **absolute, required** paths that sit as **peers of
  `docker/`**:

  ```
  /mnt/user/appdata/persona-forge/
  ├── docker/   compose + .env  (the only folder copied to the server)
  ├── db/       sqlite: personas, prompt history
  └── logs/     rolling log file
  ```

  Compose now fails fast with a clear message if `DB_HOST_PATH` or `LOGS_HOST_PATH`
  is unset. The `APPDATA_ROOT` / `APPDATA_HOST_PATH` concept is gone.

---

## [0.2.4] — 2026-07-24

**Phase 2 — Logs, and a cross-container permissions fix.**

### Added
- **Clone a persona** (`POST /api/projects/{id}/clone` + sidebar button). Copies the
  current prompt into a new persona so it can be varied — the same character
  *skiing* and *lazing on a beach*. Identity (`character`) is kept, `style` is
  editable at clone time, and `parent_project_id` is recorded so **Phase C can offer
  to reuse the parent's LoRA instead of retraining** — turning an outfit/scene
  variant from a ~1 hr training job into a prompt change.
- Personas persist and reload from the sidebar selector, with their full version
  history intact.
- **Logs tab** — a first-class view, filterable by level (`debug`/`info`/`warn`/
  `error`) and category:
  - `boot` — startup: config, db init, builds-mount check, workflow manifest
    validation
  - `integration` — ComfyUI calls: submissions, queueing, completion, failures
  - `process` — pipeline steps: project created, version saved/signed off/rolled
    back, generation start → finish
  - `local` — folder creation, ownership changes, file work
- Records go to **stdout** (`docker logs persona-forge`), an **in-memory ring** the
  UI polls, and a **rolling JSONL** in `appdata/logs/` so boot history survives a
  restart. "Load previous runs" reads that file.
- `GET /api/logs` (filters + stats) and `GET /api/logs/persisted`.
- `POST /api/projects/{id}/repair-permissions` — re-applies ComfyUI-writable
  ownership to a build folder created before this release.

### Fixed
- **Generation failed with `Permission denied` on the shared builds folder.**
  Persona Forge runs as root and created `<build>/images/` as `root:root`, which
  the ComfyUI container (a different user) could not write into. Build folders are
  now chowned to `PUID:PGID` (default `99:100`, Unraid `nobody:users`) and chmodded
  `775`, with a `0777` fallback if chown isn't permitted.

### Changed
- `PUID` / `PGID` are configurable in `docker/.env`.

---

## [0.2.3] — 2026-07-24

**Phase 2 — Prompt Studio UI.**

### Added
- Prompt Studio: project create/select, character / style / negative editor,
  checkpoint picker populated live from ComfyUI, seed with reroll, Generate with
  inline preview.
- **Version history as a VCS-style rail** — a node per version, diff tags showing
  which fields changed, `signed off` / `current` chips, and per-version *Roll back*
  and *Sign off* actions.
- Sign-off captures unsaved edits first, so the baseline always matches what is on
  screen.
- Unraid `net.unraid.docker.webui` / `icon` labels so the container gets a clickable
  WebUI link.

### Fixed
- `frontend/` and `VERSION` resolved differently in a repo checkout vs. the
  container, so the root route 404'd locally. All asset paths now go through one
  resolver.

---

## [0.2.2] — 2026-07-23

**Deployment corrected to the project convention.**

### Changed
- The stack now **pulls a prebuilt image from GHCR** instead of building from
  source, so **only `docker/` is copied to UR1** — no application source on the
  server. This matches how `blender-mcp` and `comfyui-mcp` deploy.
- Added `.github/workflows/publish-image.yml` (publishes on `v*` tag or manual run).
- `docker/docker-compose.build.yml` keeps source builds available for local dev only.

---

## [0.2.1] — 2026-07-23

### Changed
- Moved `docker-compose.yml` and `.env.example` into a **`docker/`** folder to match
  the established project layout.
- Corrected the shared builds path after the folder was renamed.

---

## [0.2.0] — 2026-07-23

**Phase 2 — backend foundations.**

### Added
- SQLite store where `prompt_versions` is **append-only**: an edit inserts a child
  row and moves a `current` pointer, so rollback is safe and a signed-off prompt
  cannot be lost.
- ComfyUI HTTP client (submit / poll / outputs / view; live model lists from
  `/object_info`).
- **Workflow templates + parameter manifests**, so node IDs aren't hardcoded in
  application code, plus `validate_manifest()` to catch drift after a workflow edit.
- First template: `base-character`.
- API: project create (makes `<builds-root>/<slug>/{lora,images}`), version
  create / sign-off / rollback, generate, image proxy, model lists.

---

## [0.1.1] — 2026-07-23

### Fixed
- Corrected the shared builds host path.

### Added
- Project rationale in the README and plan.

---

## [0.1.0] — 2026-07-23

**Phase 1 — skeleton and deploy loop.**

### Added
- FastAPI backend with health, ComfyUI status, and a **storage check that actually
  write-probes** the shared builds mount — the dependency everything else rests on.
- Static frontend shell: left sidebar with ComfyUI and Builds status pinned at the
  top.
- `docker-compose` stack, `.env.example`, and the `appdata/` layout.
- MIT licence.

[0.2.7]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.7
[0.2.6]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.6
[0.2.4]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.4
[0.2.3]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.3
[0.2.2]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.2
[0.2.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.1
[0.2.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.0
[0.1.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.1
[0.1.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.0
