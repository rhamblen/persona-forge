# v0.7.4 — Stop a running build

Phase 7. A one-click way to stop an in-progress LoRA build — and it now actually frees the GPU.

## Added
- **"Stop build" button** on the Build panel (LoRA tab), shown whenever a build is queued or
  running. Click it (there's a confirm) and the build stops. Until now the only way to stop a
  build mid-run was to hit the cancel API by hand.

## Changed
- **Stopping a running build now interrupts ComfyUI too.** The job cancel was *cooperative* — it
  halted the pipeline from advancing, but the training run already submitted to ComfyUI kept
  churning the GPU until it finished. Stop now also calls ComfyUI `POST /interrupt` and clears
  its pending queue, so the **GPU is freed right away**. New `comfy.interrupt()` /
  `comfy.clear_pending()`, wired into `POST /api/jobs/{id}/cancel`. Best-effort: if ComfyUI is
  unreachable the job is still flagged and the worker finalizes it.

## Note
A stopped build lands as **canceled** (or **error** if ComfyUI was interrupted mid-training) —
both are terminal and harmless; just start a fresh build when ready.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.4`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
