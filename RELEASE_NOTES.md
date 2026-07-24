# v0.5.1 — Logging overhaul + verbose level

Logs now cover the whole pipeline, not just boot, and there's a `verbose` firehose level.

## Added
- **`verbose` log level** below `debug` — every cross-system handshake, each file copied
  between shares, each poll. New VERBOSE chip + min-level option in the Logs tab (purple),
  and it reaches stdout too. Default view stays at INFO+, so verbose is opt-in.
- **Pipeline instrumentation**, level chosen per step:
  - **integration (verbose)** — the real handshakes: `→ ComfyUI POST prompt / upload/image /
    history`, `←` responses, Ollama request/response, history polls, with sizes/timings.
  - **process (info)** — milestones: batch queued, reconcile results, "staging N images
    /builds → ComfyUI input/…", "staged X/Y".
  - **local (verbose)** — the share copies: reading each dataset image off `/builds`, byte
    counts; **warn** if a selected image is missing on the share.
  - **warn/error** — a dataset image that failed to render, an upload ComfyUI rejected.
  - **api (verbose)** — every inbound request (`method path → status`, ms).
  - **Boot** now also handshakes ComfyUI + Ollama and logs whether each is reachable.

To see it all: Logs tab → set **Min level** to VERBOSE.

**Image:** `ghcr.io/rhamblen/persona-forge:0.5.1`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
