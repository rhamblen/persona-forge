# v0.2.6 — Consistency pass & state layout fix

## Fixed
- **Persistent state now sits as peers of `docker/`, and is no longer called
  "appdata"** (which was redundant under `/mnt/user/appdata/`). The old relative
  bind resolved against compose's project directory — which Unraid's Compose
  Manager does not reliably set to the compose folder — so it was created *inside*
  `docker/`.

  ```
  /mnt/user/appdata/persona-forge/
  ├── docker/   compose + .env  (the only folder you copy)
  ├── db/       sqlite: personas, prompt history
  └── logs/     rolling log file
  ```

  `DB_HOST_PATH` and `LOGS_HOST_PATH` are now **required and absolute**; compose
  fails fast with a clear message if either is missing.

## Added
- **`persona.json` sidecar** in every build folder — persona, current prompt,
  signed-off baselines and full version history, so a build folder is portable and
  self-describing rather than depending on the database.
- `docs/ui-style.md` records the real design tokens taken from `esp32-shutter-hub`.

## Changed
- **Logs tab restyled** to match the rest of the app — shared tile idiom with an
  accent-ring selected state, chip level badges, responsive rows.
- **README restructured** to the same shape as `esp32-shutter-hub` / `pihole-mcp`;
  `## Status` is now a short phase statement pointing at `CHANGELOG.md` rather than
  a duplicate of it.

**Image:** `ghcr.io/rhamblen/persona-forge:0.2.6`

## Upgrading
1. Re-copy `docker/`.
2. In `.env` set `DB_HOST_PATH=/mnt/user/appdata/persona-forge/db` and
   `LOGS_HOST_PATH=/mnt/user/appdata/persona-forge/logs`.
3. Move any existing `docker/appdata/db` up to `../db` to keep your personas.
4. `docker compose pull && docker compose up -d`
