# v0.5.2 — The LoRA actually trains

Phase 5. Released after 0.6.0 because LoRA and Poses are being built in parallel — version
tags interleave, as expected.

## Added
- **Train a character LoRA** from your staged dataset, end-to-end in ComfyUI. A new **Train**
  section on the LoRA tab (steps / rank / learning-rate — defaults 500 / 16 / 5e-4) and a
  **Train LoRA** button, with live status (`training…` / `done` / `failed`) that polls until
  the run finishes. The `.safetensors` then appears under **Trained LoRAs**.
- The graph is the **native ComfyUI `TrainLoraNode`** pipeline (load dataset → batch →
  VAE-encode → trigger conditioning → train → save). **Validated with a real training run**
  before shipping — the wiring is confirmed, not guessed.
- **Frees VRAM before training** (unloads the Ollama model + ComfyUI's models). Training OOMs
  otherwise when the 3090 is already loaded — observed and handled.

## Loading the trained LoRA (one-time ComfyUI config)
`SaveLoRA` writes to the build folder (`<slug>/lora/`), which is ComfyUI's **output** dir —
not its `models/loras`. So a trained LoRA won't show in ComfyUI's loras dropdown until you
tell ComfyUI to also look in the builds root. Add this to ComfyUI's
`extra_model_paths.yaml` (next to its config, e.g. `…/ComfyUI/extra_model_paths.yaml`) and
restart ComfyUI:

```yaml
persona_forge:
  loras: /builds
```

(`/builds` is the container path already mapped into ComfyUI.) After that, trained LoRAs
appear as `<slug>/lora/<trigger>` in the loras list.

## Notes
- Captions are the **trigger word only** for now (the validated minimal path); per-image
  Florence2 light captions are next.

**Image:** `ghcr.io/rhamblen/persona-forge:0.5.2`

## Upgrading
No compose changes since 0.3.0. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
