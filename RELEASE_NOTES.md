# v0.7.1 — Prompt Studio fixes

Phase 7. Two fixes to new-persona setup.

## Fixed
- **New personas start with default negatives, not a blank field.** Fresh projects used to
  store an empty negative prompt (blank field, low-quality renders). They now seed the canonical
  starter negative — read from the `base-character` template so there's one source of truth,
  exposed at `GET /api/prompt-defaults` — pre-filled and fully editable.
- **Per-persona version numbers.** The version rail / current-version chip showed the global row
  id (a new character could open at "v37"). Each project's versions now number from **v1** by
  creation order; the real id is still used for API calls.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.1`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
