# v0.7.7 — Fix: stopped build jamming future builds

Phase 7. A bugfix for the exact symptom you hit: after a stopped build, every new build failed
instantly with *"a training run is already in progress for this persona."*

## What was wrong
A build cancelled during the training stage left the project flagged `train_status = 'training'`
with no ComfyUI prompt behind it. The reconciler needs a prompt id to clear that flag, so it
stayed stuck forever — blocking every future build for that persona.

## Fixed
1. **Auto-heal.** The reconciler now clears an orphaned `training` flag based on ComfyUI reality
   (a real run always has a prompt id, so a missing one is stale; a vanished prompt is stale once
   ComfyUI's queue is idle). It runs right before the "already training" check, so simply
   **clicking Build again self-heals** — you don't even need to open the LoRA tab.
2. **Stop resets the flag.** Cancelling a running build now takes the project out of `training`
   as part of the stop, so it can't get stuck again.

## After you deploy
Your stuck **monster-girl** project clears itself the moment you open its LoRA tab or hit Build.
Just re-run the build (it's on 113 selected images, dataset already staged).

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.7`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
