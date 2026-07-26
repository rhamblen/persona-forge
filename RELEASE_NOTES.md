# v0.7.9 — Style LoRAs in the Prompt Studio

Phase 7. Apply an external style/detail LoRA on top of the checkpoint while you compose a persona.

## Added
- **Style LoRA picker in the Prompt Studio.** A new *Style LoRA* dropdown (populated from
  ComfyUI's `loras` folder) plus a strength slider sit under the Checkpoint field. Pick one and
  Generate renders the checkpoint **with that LoRA loaded**; leave it on *none* and nothing
  changes — it's the same checkpoint-only render as before.
- The choice is **saved per version** (`style_lora` + `style_lora_strength`), so it lives in the
  append-only history, shows up in the version diff, and rolls back with everything else.
- **Dataset generation honours it too** — when a version has a Style LoRA selected, the training
  dataset is rendered through it, so the look you dialled in actually makes it into the trained
  character LoRA instead of vanishing at build time.

## How it works
- A new `base-character-lora` workflow loads the LoRA through a **full `LoraLoader`** (model **and**
  CLIP), so text-encoder-side style LoRAs behave correctly. Studio and dataset generation upgrade
  from `base-character` to `base-character-lora` automatically when a LoRA is set, and render
  identically to before when none is. One strength drives both the model and CLIP sides.
- This is distinct from the **Poses** tab's *character* LoRA (a model-only `LoraLoaderModelOnly`
  patch of your own trained identity) — that path is unchanged.

## Changed
- `POST /api/projects/{id}/generate` accepts optional `style_lora` / `style_lora_strength`.
- `prompt_versions` gains `style_lora` (TEXT) and `style_lora_strength` (REAL) columns; older
  databases are migrated automatically on boot (existing versions default to no LoRA).

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.9`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
