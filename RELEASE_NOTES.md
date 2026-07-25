# v0.7.5 — Delete dataset candidates

Phase 7. Clean up the dataset after cherry-picking: purge everything you didn't select, or drop
individual pictures.

## Added
- **"Purge unselected" button** on the Dataset tab. Once you've picked your keepers, one click
  removes **every unselected candidate** — DB rows *and* the image files on `/builds` — leaving
  just your training set. The button shows the live count ("Purge 12 unselected") and disappears
  when there's nothing unselected. `POST /api/projects/{id}/dataset/purge`.
- **Per-candidate delete.** Every dataset thumbnail now has a 🗑 badge (appears on hover) to
  delete that single image — selected or not. `DELETE /api/projects/{id}/dataset/{image_id}`.

## Notes
- Both confirm first and are **irreversible** — the files are unlinked from `/builds`.
- Deletion is guarded against escaping the builds root and is best-effort: a candidate always
  leaves the dataset even if its file was already gone.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.5`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
