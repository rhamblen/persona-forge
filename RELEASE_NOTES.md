# v0.6.1 — Export poses to SillyTavern sprites

Phase 6. Turns a rendered pose/expression set into SillyTavern-ready sprites.

## Added
- **Export to SillyTavern** on the Poses tab. Each rendered pose is matted to a
  **transparent PNG** (BEN2) and named for SillyTavern — an exact expression name (`joy`,
  `anger`, …) is kept verbatim so ST recognises it; anything else is slugified; duplicate
  names are de-duped so nothing overwrites. Sprites are written to `<build>/export/<Character>/`
  and are **staged only** — copy them into your character's SillyTavern folder yourself;
  nothing is written to ST automatically. Queued and reconciled from ComfyUI history.
- New `bg-remove` workflow: BEN2 background removal + WAS `Image Save`
  (`prefix_as_filename`) for exact `<name>.png` filenames — the matte path proven to work on
  this ComfyUI. New `export_jobs` table; endpoints under `/api/projects/{id}/poses/export`.

**Image:** `ghcr.io/rhamblen/persona-forge:0.6.1`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
