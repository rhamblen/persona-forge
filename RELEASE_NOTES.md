# v0.7.8 — LoRA build dates

Phase 7. Know which LoRA is the fresh one after a rebuild.

## Added
- **Build date on every trained LoRA.** The LoRA tab lists each `.safetensors` with its build
  time (the file's modified time, which bumps every rebuild), **newest first**, and tags the most
  recent one **latest**. After a refresh/retrain you can confirm at a glance you're on the fresh
  version — not a stale one.
- The **Poses** Character-LoRA dropdown shows each LoRA's date, and the selected-LoRA hint reads
  "…(built <date>)", so you can verify the pose set is rendering with the refreshed LoRA.

## Changed
- `GET /api/projects/{id}/lora` and `.../pose-config` now return each LoRA as
  `{name, modified, modified_ts, size, comfy_visible}` (newest first) instead of a bare name.

## Note
The date is the file's modified time — reliable and works for LoRAs you've already built. A
richer *embedded* build stamp (version + steps + dataset size written alongside the file) is a
possible follow-up.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.8`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
