# v0.2.7 — Ship a ready-to-use docker/.env

## Changed
- **`docker/.env` is now a real, tracked file** rather than `.env.example`. Copying
  `docker/` to the server gives a working stack with no rename step.

  It holds only non-secret config (ComfyUI URL, host paths, port, `PUID`/`PGID`,
  `TZ`). `.gitignore` still ignores every other `.env`, with one explicit exception
  for this file — if a credential is ever needed it must move out of version
  control and that exception be dropped.

**Image:** `ghcr.io/rhamblen/persona-forge:0.2.7`

## Upgrading
Re-copy `docker/`, check the paths in `docker/.env`, then:

```bash
docker compose pull && docker compose up -d
```
