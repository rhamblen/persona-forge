# Persona Forge — Project Plan

> A self-hosted web app that turns the ad-hoc ComfyUI character workflow into a
> guided pipeline: **prompt → dataset → per-character LoRA → pose/expression
> sets**, with natural-language editing and full prompt rollback.
> Living document — edit freely.

Repo: https://github.com/rhamblen/persona-forge

Created 2026-07-23. Sibling to the VRM/Live2D avatar work in the parent repo's
`PROJECT_PLAN.md` (that track is 3D avatars; this track is 2D sprites + LoRAs).

---

## 1. Goal

Type a character description, refine it visually, and come out the other end with
a **trained per-character LoRA** plus a **full expression/pose sprite set** ready
for SillyTavern — without hand-driving ComfyUI. Everything runs on the LAN
(ComfyUI + Ollama on UR1); no Claude Code in the runtime loop.

## 2. The pipeline (core UX)

Four phases, each gating the next. The user's spec, formalised:

### Phase A — Prompt Studio
- Enter a prompt (prose or tags); **pick the checkpoint/model**.
- Fire a single image; **refine the prompt** and re-fire until happy.
- **Natural-language edits** (via Ollama): "make her hair shorter, add glasses" →
  proposed prompt change → accept/reject.
- **Sign off** → the approved prompt + model + seed become a **locked baseline**
  that can never be lost (see Rollback, §4).

### Phase B — Dataset Builder
- Generate a batch (default **30**) from the signed-off prompt, varied seeds/framing.
- Show them in a **selectable grid**; user picks the ones that look like the *same
  person*.
- If the selection is short of the target N, generate **+10 more** and repeat until
  N reached.
- Selected images become the **training dataset** for this character.

### Phase C — LoRA Trainer
- Auto-caption the dataset, configure, and **train a per-character LoRA** on the
  3090.
- Monitor progress; on completion the LoRA is registered and selectable.
- (One LoRA per character — this is the price of true consistency + pose freedom.)

### Phase D — Pose / Expression Studio
- Using the LoRA, generate the **expression + pose set** (the 28 SillyTavern
  expressions, and/or pose variants).
- Present them all in a grid; user selects any to **tweak — pose OR face**
  independently.
- Tweaks use the same NL editing + rollback. **Export** the finished set (correct
  SillyTavern filenames, transparent PNGs).

## 3. Cross-cutting features

- **Ollama NL assistant** — one bounded job: given the current prompt + a plain-
  language instruction, return a revised prompt (+ a human-readable diff). Also
  handles prose↔tag translation. Runs as its own container; a small instruct model
  is enough.
- **Model + LoRA selection** — checkpoint picker in Phase A; LoRA picker in Phase D.
- **Export to SillyTavern** — write the set into a `<Character>/` folder with exact
  expression filenames (staged for the user to copy to the ST appdata — same
  copy-in gotcha as the VRM assets).

## 4. Rollback / prompt versioning (hard requirement)

- Every prompt is an **append-only version** (id, parent, text, model, seed,
  source = manual|ollama, created_at).
- **Sign-off** pins a version as the baseline; it is immutable and always
  restorable.
- Any edit (manual or NL) creates a **child version**; the UI shows a timeline and
  lets you **roll back to any prior version**, especially the baseline.
- Nothing is ever destructive — "we never lose the agreed prompt we signed off on."

## 5. Architecture

```
┌────────────┐   HTTP    ┌─────────────────────┐   HTTP    ┌──────────────┐
│  Frontend  │◄─────────►│   Backend (FastAPI) │◄─────────►│  ComfyUI     │ UR1:9000
│  (React)   │  /api     │  orchestration +    │  /prompt  │  (existing)  │
│            │           │  state + versioning │  /history └──────────────┘
└────────────┘           │                     │   HTTP    ┌──────────────┐
                         │                     │◄─────────►│  Ollama      │ UR1
                         │                     │  /api/chat│  (new)       │
                         │                     │           └──────────────┘
                         │                     │   train_* ┌──────────────┐
                         │                     │◄─────────►│ ComfyUI-MCP  │ UR1:8878
                         └─────────┬───────────┘  or nodes │ (existing)   │
                                   │ SQLite + files        └──────────────┘
                                   ▼
                            appdata/ (persistent)
```

- **Backend — Python / FastAPI.** Async fits polling ComfyUI + calling Ollama.
  Owns: workflow submission (reusing our saved JSON templates), job polling, image
  fetch, prompt version store, dataset/LoRA/pose-set records, export.
- **Frontend — React (Vite + Tailwind)**, built into the backend's static dir so
  the whole thing ships as one image. Galleries with multi-select, prompt editor
  with version timeline, phase wizard.
- **ComfyUI — existing**, UR1:9000. We already have the workflow templates (base
  gen, IPAdapter poses, 28-expression, single re-roll). Backend parameterises and
  submits them.
- **Training — reuse the existing ComfyUI-MCP `train_*` flows** if they fit (they
  exist: `train_prepare_dataset`, `train_start`, `train_status`, …), else a small
  kohya/sd-scripts container. Decide during M3.
- **Ollama — new container.** Watch VRAM: it shares the GPUs with ComfyUI, so
  load/unload around generation or pin it to the 3060.
- **Storage — SQLite** (state, prompt history) + **filesystem** (images, datasets,
  LoRAs, exports), all under `appdata/`.

## 6. Repo & deploy structure

Follows the firm UR1 convention (memory `feedback-ur1-docker-deploy-convention`):
Claude edits local + instructs; **the user copies `appdata/` to UR1 and builds via
the Docker Compose Manager addon; Claude never builds/deploys, only verifies.**

```
persona-forge/                    # GitHub repo
├── README.md
├── PROJECT_PLAN.md               # this file
├── docker-compose.yml            # webui (+ optional ollama); ComfyUI is external
├── .env.example                  # COMFYUI_URL, OLLAMA_URL, ports, paths
├── backend/                      # FastAPI app  (Claude edits)
│   ├── app/ …
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                     # React app    (Claude edits) → built into backend
│   └── …
├── appdata/                      # <── USER copies this to /mnt/user/appdata/persona-forge/
│   ├── config/                   #     settings, endpoints
│   ├── db/                       #     sqlite (state + prompt history)
│   ├── datasets/                 #     selected training images, per character
│   ├── loras/                    #     trained LoRAs
│   └── output/                   #     generated sprites / pose sets / exports
└── docs/
```

- **Image delivery:** prefer publishing to **GHCR via a GitHub Action** (like
  `blender-mcp`) so the user's build is a light `compose pull`; source-build on UR1
  is the fast-iteration fallback.
- **ComfyUI stays external** — the stack points at UR1:9000, it does not bundle it.

## 7. Build roadmap (incremental, each milestone demoable)

- **M0 — Skeleton & deploy loop.** Repo scaffold, `docker-compose.yml`, a hello
  FastAPI + static React, `appdata/` layout. Prove the copy→compose→verify loop
  end to end with an empty app.
- **M1 — Prompt Studio (Phase A).** Model picker, single-image generate against
  ComfyUI, prompt editor, **version store + sign-off + rollback**. (No Ollama yet.)
- **M2 — Ollama NL editing.** Stand up Ollama; wire "instruction → revised prompt +
  diff → accept/reject → new version." Prose↔tag helper.
- **M3 — Dataset Builder (Phase B).** Batch-30 gallery, multi-select, +10 top-up
  loop, persist the selected dataset. **Decide the training backend here.**
- **M4 — LoRA Trainer (Phase C).** Auto-caption, train, monitor, register the LoRA.
  First run = recipe calibration (dataset size, steps, LR).
- **M5 — Pose/Expression Studio (Phase D).** LoRA-driven expression/pose set, grid,
  per-sprite tweak (pose OR face) with NL + rollback, **SillyTavern export**.
- **M6 — Hardening.** GHCR image + Action, backups of `appdata/db`, README/run docs,
  polish.

## 8. Open decisions (need your call)

- ~~**Name**~~ — decided: **Persona Forge**.
- **Frontend weight** — full React SPA (richer galleries) vs. a lighter HTMX/Svelte
  build. Recommendation: React, but open. **Style to follow the user's references
  (e.g. Shutter Hub) — see `docs/ui-style.md`.**
- **Training backend** — ComfyUI-MCP `train_*` flows vs. a dedicated kohya
  container (decided at M3 once we test the former).
- **Ollama model** — a small instruct model with good JSON/prose handling that
  coexists with ComfyUI on the GPUs (candidate: a Qwen/Llama instruct variant).
- **GHCR now or later** — publish images from the start, or source-build on UR1
  until the app stabilises.

## 9. Risks & honest realities

- **LoRA consistency is the whole game.** IPAdapter alone gave "recognisable but
  drifty" across 4 tuning passes (hair/outfit/proportions wobble). The LoRA is what
  fixes this — but its quality depends on the **dataset cherry-pick** (a human
  step) and a **first-run calibration**. Budget ~1.5–2.5 hrs for the first
  character, faster after.
- **VRAM contention** — Ollama + ComfyUI (+ training) on the same GPUs. Plan to
  serialise heavy steps or pin Ollama to the 3060.
- **Region/asset gotchas carry over** — Civitai is 451-blocked from UR1; Manager
  3.x blocks URL model installs; `custom_nodes` is read-only over SMB
  (memory `project-comfyui-ur1-paths-and-env`). Model/LoRA installs go through the
  known-good routes.
- **Scope** — this is a multi-week build. M0–M2 deliver a genuinely useful Prompt
  Studio on their own, so value lands early even if later phases slip.

---
*Related memory: `project-persona-forge`,
`feedback-ur1-docker-deploy-convention`, `project-live2d-expression-workflow`,
`feedback-prose-prompts-over-tags`, `project-comfyui-ur1-paths-and-env`.*
