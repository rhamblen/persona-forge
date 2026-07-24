# v0.4.1 — Zoom dataset candidates

## Added
- **Zoom on dataset candidates.** Each thumbnail in the Dataset tab now has a hover **⤢**
  badge that opens the full image in the lightbox (click the backdrop or press Esc to
  close), so you can examine a snap closely while selecting. The zoom badge is separate from
  the click-to-select body — zooming never changes your selection.

**Image:** `ghcr.io/rhamblen/persona-forge:0.4.1`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
