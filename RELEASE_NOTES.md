# v0.5.3 — Per-image auto-captioning for training

Phase 5. Completes the "trigger word + light caption" scheme you chose.

## Changed
- **Training now auto-captions each dataset image with Florence-2** instead of using the
  trigger word alone. The training graph gained an inline caption stage:
  `Florence2ModelLoader` + `Florence2Run(task=caption)` per image →
  `StringConcatenate` prefixes your **trigger word** (`pf_<slug>, <caption>`) →
  per-image `CLIPTextEncode` → `TrainLoraNode`. The LoRA still binds to the trigger word, but
  the per-image captions help it separate the character's identity from pose and background.
- **Validated end-to-end with real training runs** — including that `TrainLoraNode` accepts
  the per-image conditioning matched to the image batch (that was the open question; it does).
- No app/API change — the train endpoint already passes the trigger; only the workflow
  template changed. The first run loads Florence-2 (a little extra VRAM + time); VRAM is still
  freed before training.

**Image:** `ghcr.io/rhamblen/persona-forge:0.5.3`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```

Still applies from 0.5.2: to load a trained LoRA in ComfyUI, add `loras: /builds` to its
`extra_model_paths.yaml` and restart ComfyUI.
