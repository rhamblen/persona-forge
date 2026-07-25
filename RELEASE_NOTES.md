# v0.6.2 — LoRA-driven poses, training timer, clearer export

Phase 6. The joint **LoRA-into-poses** deliverable, plus a couple of quality-of-life wins.

## Added
- **Pose renders can load the trained character LoRA.** New `pose-with-lora` workflow
  (`CheckpointLoaderSimple → LoraLoaderModelOnly → KSampler`) with your **trigger word
  prepended** to the prompt. The Poses tab gained a **Character LoRA** selector (with a
  strength control): pick a trained LoRA per project and pose/expression variants stay
  on-model; leave it on *None* and poses render from the base character as before. New
  endpoints `GET /pose-config` and `POST /pose-lora`.
  - The selector flags a trained LoRA that exists on disk but isn't visible to ComfyUI yet and
    tells you to add `persona_forge: { loras: /builds }` to ComfyUI's `extra_model_paths.yaml`
    and restart — the one manual prerequisite.
- **Training timer + ETA.** The LoRA tab records a start time and logs the **run duration** (and
  s/step) at `info` on completion, so past training times are searchable in the log. While a
  run is going it shows **elapsed time and an ETA** from the previous run.

## Changed
- **Export panel is now "Export to builds folder"** (was "Export to SillyTavern"). It always
  staged sprites into the build folder for you to copy into SillyTavern by hand — the label now
  says so. No behaviour change.

## Fixed / Notes
- Training shares UR1's RTX 3090 with other GPU containers (ollama, chatterbox-st, immich,
  a-eye). If they hold VRAM, `TrainLoraNode` can OOM even though ComfyUI frees its own memory
  first. **Stop the aux GPU containers (or let Ollama evict) before a training run** until they
  are moved to the idle RTX 3060.

**Image:** `ghcr.io/rhamblen/persona-forge:0.6.2`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```

To actually load a trained LoRA (training **or** the new LoRA-driven poses), add `loras: /builds`
to ComfyUI's `extra_model_paths.yaml` and restart ComfyUI so the LoRAs appear in its list.
