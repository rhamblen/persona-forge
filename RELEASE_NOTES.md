# v0.3.3 — Reject AI changes one at a time

Follow-up to the AI diff: you can now accept or reject each change individually.

## Added
- **Per-change accept/reject.** Every change in the AI diff has its own **✕** button —
  reject a single change and only that span reverts to the previous text, keeping the rest
  of the suggestion. A rejected change is shown dashed-outlined with its addition ghosted,
  and its button flips to **↺** to re-apply it — every choice is reversible. "Reject all &
  undo" is still there for the bulk case. Hand-editing a field retires that field's diff so
  a later reject can't overwrite your manual edit; other fields stay live. All client-side.

**Image:** `ghcr.io/rhamblen/persona-forge:0.3.3`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
