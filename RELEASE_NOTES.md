# v0.3.1 — House-standard log page + stale-cache fix

A polish release: the Logs tab now matches the esp32-shutter-hub web-UI log page, and
browsers no longer serve a stale frontend after a deploy.

## Changed
- **Logs tab reskinned to the house standard** — a dark monospace terminal
  (`[time] LVL category: message`, level-coloured), a **Min level** dropdown plus
  **colour-coded level toggle-chips**, a **category chip row** (`boot`/`integration`/
  `process`/`local`), a buffered **count** pill, a **live** state indicator, **Auto-scroll**,
  **Clear**, and **Previous runs** (the persisted file). Structured detail shows inline,
  dimmed. Filtering is client-side over the last 500 entries.

## Fixed
- **Stale `app.js` after a deploy.** The frontend is now served with
  `Cache-Control: no-cache`, so a browser always revalidates and picks up a new build on
  the next load (unchanged assets are still cheap 304s). This is what caused the Ollama
  sidebar to sit on "checking…" against a healthy 0.3.0 backend. **After this build lands,
  future updates no longer need a hard refresh.**

**Image:** `ghcr.io/rhamblen/persona-forge:0.3.1`

## Upgrading
No compose changes since 0.3.0. Re-copy `docker/` (or just pull), then:

```bash
docker compose pull && docker compose up -d
```

Note: to leave the *stale-cache* state you may be in right now, this one time still needs a
hard refresh (Ctrl+Shift+R) — from the next update onward the no-cache header handles it.
