# v0.6.0 — Pose / Expression studio (Phase 6)

Phase 6 opens. Built in parallel with the LoRA phase, so version tags interleave with any
further 0.5.x LoRA work — that's expected.

## Added — Poses tab
1. **All created.** Add poses individually, or load a preset — a **Starter set** of 8 body
   poses, or the **28 SillyTavern expressions** — then **Generate all**. Each renders from the
   project prompt + the pose's modifier; rendering is queued and the grid fills in as ComfyUI
   finishes (tracked in a `poses` table, reconciled from history like the dataset builder).
2. **Select → zoom.** Click a pose to open its editor; the preview (and a Zoom button) open
   the image full-size in the lightbox.
3. **Modify.** With a pose selected, edit its **modifier** by hand *or* ask the AI
   ("make her sit cross-legged" → Ollama revises just that fragment), then **Save &
   regenerate** that one pose. The editor is available whenever a pose is selected — zoom is
   optional, not required, to make a change.

Endpoints under `/api/projects/{id}/poses`; new `poses` table (created on boot).

## Fixed
- Logs: widened the level column so `VERBOSE` no longer wraps.

## Notes
- Poses currently render from the base prompt + modifier. Once the LoRA phase lands, pose
  generation will use the trained character LoRA for on-model consistency.

**Image:** `ghcr.io/rhamblen/persona-forge:0.6.0`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
