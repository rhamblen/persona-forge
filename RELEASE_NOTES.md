# v0.3.2 — See what the AI changed

A small usability release for the AI prompt assistant.

## Added
- **Word-level diff on AI suggestions.** After Replace or Modify, a per-field diff appears
  under the AI box: **added/changed text highlighted green**, **removed text in red
  strikethrough**. Unchanged fields are omitted, so edits are easy to scan — and any word
  the model *dropped* shows up in red. Modify is instructed to preserve untouched text and
  can never blank a whole field, but it is a local model, so the red diff is the real
  safeguard. The suggestion still lands in the editable fields with the existing
  reject-and-undo. Diff is computed client-side.

**Image:** `ghcr.io/rhamblen/persona-forge:0.3.2`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
