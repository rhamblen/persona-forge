# v0.2.5 — Consistency pass

**Docs, UI language, and self-describing builds.**

## Added
- **`persona.json` sidecar** in every build folder — persona, current prompt,
  signed-off baselines and full version history. Build folders are now portable and
  self-documenting; previously, copying one (or losing the db) kept the images but
  lost the prompt that produced them.
- `docs/ui-style.md` records the real design tokens taken from `esp32-shutter-hub`.

## Changed
- **Logs tab restyled** to match the rest of the app — shared tile idiom with an
  accent-ring selected state, chip level badges, responsive rows.
- **README restructured** to the same shape as `esp32-shutter-hub` / `pihole-mcp`.
- **`## Status` no longer duplicates the changelog** — it now states the current
  phase and links to `CHANGELOG.md`.

**Image:** `ghcr.io/rhamblen/persona-forge:0.2.5`

**Upgrade:** `docker compose pull && docker compose up -d`
