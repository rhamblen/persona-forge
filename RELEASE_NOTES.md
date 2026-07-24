# v0.3.4 — Modify stops over-editing

## Fixed
- **Modify no longer deletes text it wasn't asked to touch.** A small instruction like
  "make eyes green" could make the change but also drop unrelated sentences and rewrite the
  style/negative fields. The Modify instruction to Ollama is now strict: reproduce the
  current text **verbatim** and change only the specific words the instruction is about — no
  rephrasing, reordering, shortening, or dropping anything else. A field the instruction
  doesn't mention comes back exactly as given. Verified live: "make eyes green" changes only
  the eye colour and leaves style/negative untouched. Any straggler the model still slips
  can be dismissed with the per-change ✕ (0.3.3).

**Image:** `ghcr.io/rhamblen/persona-forge:0.3.4`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
