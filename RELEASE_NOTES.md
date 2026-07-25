# v0.7.0 — Unattended builds (background job engine)

Phase 7. The pipeline stops being a click-through-each-step UI and becomes a **walk-away job
runner**: kick off a build and close the tab — the server finishes it.

## Added
- **Generic background job engine.** A single in-process worker drains a persisted job queue,
  advancing the running job stage-by-stage until done — **browser-independent**. It's
  kind-agnostic on purpose: the lorebook generator, cast/campaign builder, and source ingestion
  (Phase E/F/G) plug in later as new handlers with no engine changes. Jobs are **resume-safe** —
  a container restart re-reconciles the running job rather than losing it.
- **`lora_build` — the first handler.** One click: **train the LoRA → auto-apply it → render the
  first-draft 28 expressions.** If ComfyUI can't see the freshly trained LoRA, the build restarts
  ComfyUI to bind it, then renders; if it still can't bind, it degrades to base-character poses
  and says so — you always wake up to a finished draft.
- **"Build overnight" panel** on the LoRA tab (steps / rank / strength + live progress).
- Job API: `POST/GET /api/projects/{id}/jobs`, `GET /api/jobs`, `GET/POST /api/jobs/{id}[/cancel]`.

## Notes
- Manual **Train** and **Generate all** still work — the engine reuses the same code.
- Deferred to Phase F (post-1.0): the multi-character **add-to-queue** cast builder and
  concurrency lanes — both ride this engine.
- Reminder: for the build to bind a LoRA, ComfyUI needs `loras: /builds` in its
  `extra_model_paths.yaml`. The build handles the required restart itself when it can.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.0`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
