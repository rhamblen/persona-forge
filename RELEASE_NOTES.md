# v0.7.6 — Target the dataset, and a lot more variety

Phase 7. Two dataset upgrades: aim a batch at a specific weak axis, and a much bigger range of
faces and poses to draw from.

## Added
- **Variety mode selector** on the Dataset tab. Choose what a batch spreads across so you can
  reinforce whatever looks weak:
  - **Both** (default) — alternates close-up faces and full-body poses (~50/50).
  - **Faces** — close-up/bust shots at varied head angles × the full range of expressions. Use
    it when the face is weak or you want more expression coverage.
  - **Poses & views** — full body from many angles and actions. Use it when the character can't
    hold poses or you want more coverage around the body.
  - **Off** — same framing, seed only.
  New `mode` field on `POST /api/projects/{id}/dataset/generate`.

## Changed
- **Much larger variety sets.** Framings split into a **face** pool (9 head angles) and a
  **body** pool (**24** poses/views: front, back, both sides, 3/4 front & back, low/high angle,
  walking, walking away, running, sitting on the floor or a chair, kneeling, crouching, leaning,
  arms crossed, hands on hips, arms raised, jumping, cowboy shots, twisting, waving). Expressions
  grew from 10 to **18**. Full-body shots use light expressions (the face is tiny there) so the
  focus stays on the pose.

## How to use it
See a weak face on the trained LoRA? Generate a **Faces** batch and add those. Can't get it to
sit or turn around? Generate a **Poses & views** batch. Everything reconciles into the same
dataset — cherry-pick, purge the rest (v0.7.5), retrain.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.6`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
