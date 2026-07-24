# v0.5.0 — LoRA trainer foundation (Phase 5)

Phase 5 opens. Training will run as a **ComfyUI workflow** (`TrainLoraNode` + Florence2
captioning) — no separate trainer container. This release lays the groundwork; captioning
and the training run follow in 0.5.1 / 0.5.2.

## Added
- **LoRA tab.** Selected-image count, an editable **trigger word** (the token the trained
  LoRA binds to, default `pf_<slug>`), staged status, and any trained LoRAs in `{slug}/lora/`.
- **Dataset staging.** "Stage dataset to ComfyUI" uploads your selected images into ComfyUI's
  `input/pf-<slug>` folder over its HTTP `/upload/image` API, so the native dataset loader can
  read them **without any extra mount** (ComfyUI's input dir isn't on the shared `/builds`
  volume — validated that the loader picks up the uploaded folder immediately).
- Endpoints under `/api/projects/{id}/lora` (status / trigger / stage); new
  `projects.trigger_word` column (auto-migrated on boot).

**Image:** `ghcr.io/rhamblen/persona-forge:0.5.0`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```

Next: 0.5.1 auto-captioning (Florence2, trigger-word + light caption), then 0.5.2 the
`TrainLoraNode` run with progress monitoring.
