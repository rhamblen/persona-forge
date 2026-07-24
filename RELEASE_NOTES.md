# v0.4.0 — Dataset builder (Phase 4)

Phase 4 opens. Persona Forge can now build the per-character training set.

## Added
- **Dataset tab.** From the current prompt, **Generate 30** candidate images (or **+10
  more**), each at a fresh random seed, then pick the ones that look like the *same person*
  in a selectable thumbnail grid. Selected images become this character's training set for
  the upcoming LoRA phase.
  - Generation is **queued, not blocking**: the batch is submitted to ComfyUI and the grid
    fills in as each image finishes. A `dataset_jobs` table tracks the in-flight batch so a
    restart doesn't lose it; finished prompts are reconciled from ComfyUI history into the
    `images` table.
  - A per-project **target N** (default 20) with a progress bar and a live
    "generating… N left in queue" indicator.
  - New endpoints under `/api/projects/{id}/dataset` (generate / list / select / target);
    new `projects.dataset_target` column and `dataset_jobs` table (auto-migrated on boot).

**Image:** `ghcr.io/rhamblen/persona-forge:0.4.0`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```

The database migrates itself on boot (adds `dataset_target` + `dataset_jobs`). Next up:
Phase 5, the LoRA trainer.
