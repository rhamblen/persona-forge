# Handover — LoRA tab folds into Poses/Phase 6 development

**Decision (2026-07-25):** further **LoRA-tab work is no longer a separate stream**. It is
handed to the Poses/Phase-6 session and done *in conjunction* with pose code, because the two
meet at one point: **pose generation must use the trained LoRA**, which it does not yet. Read
this, then `docs/ai-context.md` for the full project brief.

---

## Where LoRA (Phase 5) is — DONE through v0.5.3

The LoRA tab is functionally complete and **validated with real training runs**:

- **Trigger word** — editable per project, default `pf_<slug>`. Stored in
  `projects.trigger_word`. This is the token the LoRA binds to.
- **Stage dataset** — uploads the *selected* dataset images (from the Dataset tab) into
  ComfyUI's `input/pf-<slug>/` over the HTTP `/upload/image` API (no mount needed; ComfyUI's
  input dir isn't on the shared `/builds` volume).
- **Train** — steps / rank / learning-rate → native ComfyUI **`TrainLoraNode`** pipeline.
  The train endpoint **frees VRAM first** (unloads Ollama + ComfyUI `/free`) or it OOMs.
  `workflows/lora-train.json` (validated graph):
  `CheckpointLoaderSimple` → `LoadImageDataSetFromFolder(pf-<slug>)` →
  **`Florence2ModelLoader` + `Florence2Run(task=caption)` per image** →
  `StringConcatenate` (prefixes the trigger: `pf_<slug>, <caption>`) → per-image
  `CLIPTextEncode` → `ImageListToImageBatch` + `VAEEncode` → `TrainLoraNode` → `SaveLoRA`.
- **Trained LoRAs** — listed in the tab from `BUILDS_ROOT/<slug>/lora/*.safetensors`.

**Endpoints:** `GET /api/projects/{id}/lora`, `.../lora/trigger`, `.../lora/stage`,
`.../lora/train`. State on `projects`: `trigger_word`, `train_prompt_id`, `train_status`
(reconciled from ComfyUI history in the lora GET).

### The one manual prerequisite (user does this in ComfyUI, once)
`SaveLoRA` writes to ComfyUI's **output** dir (`/builds/<slug>/lora/`), **not** `models/loras`,
so trained LoRAs are NOT in ComfyUI's loras dropdown until the user adds to ComfyUI's
`extra_model_paths.yaml` and restarts ComfyUI:

```yaml
persona_forge:
  loras: /builds
```

After that, a trained LoRA is loadable as `lora_name = "<slug>/lora/<trigger>_<steps>_.safetensors"`
(SaveLoRA appends a step suffix — glob the folder / use the lora GET's `loras` list to get the
exact filename; don't hardcode it).

## Where Poses (Phase 6) is — DONE through v0.6.1

- **0.6.0 Poses tab** — add poses / presets (Starter 8 body poses, or the 28 ST expressions),
  **Generate all** (queued + reconciled via the `poses` table), **select → zoom → edit**
  (modifier by hand or `ollama.revise()`), **Save & regenerate** one pose.
- **0.6.1 Export** — BEN2 matte each rendered pose → transparent PNG named for SillyTavern,
  staged in `<build>/export/<Character>/`, never auto-copied. `workflows/bg-remove.json`,
  `export_jobs` table.
- Pose renders currently use `workflows/base-character.json` with the pose's `modifier` fed
  to the `expression` param. **No LoRA is loaded.**

---

## WHAT HAPPENS NEXT — the joint work (do this in the Poses session)

**1. Wire the trained LoRA into pose generation (the whole point of consolidating).**
   Pose images should be generated *with the character's LoRA* so posture variants stay
   on-model (this is the core project bet — see PROJECT_PLAN §9). Concretely:
   - Add a LoRA loader to the pose-generation graph — insert `LoraLoaderModelOnly` (or
     `LoraLoader` for model+clip) after `CheckpointLoaderSimple`, feeding the model (and clip)
     downstream. Either edit `base-character.json` to optionally take a `lora_name` +
     `lora_strength`, or make a `pose-with-lora.json` variant. A manifest param `lora_name`
     wired to that node, `""`/None = skip (bypass) so prompt-studio previews still work
     LoRA-free.
   - **Prepend the trigger word** to the pose prompt when a LoRA is used (the LoRA binds to
     it). The trigger is `projects.trigger_word`.
   - Pick the LoRA per project from `BUILDS_ROOT/<slug>/lora/*.safetensors` (the lora GET
     already lists them). Only offer it when `train_status == 'done'` and a file exists.
   - Requires the `extra_model_paths` step above; surface a hint in the UI if the LoRA file
     exists on disk but isn't in ComfyUI's `LoraLoaderModelOnly` list (means the user hasn't
     added the path / restarted ComfyUI).

**2. Optional LoRA polish (lower priority):**
   - **Reuse parent LoRA for clones** — `projects.parent_project_id` is set on clone; a clone
     can point pose generation at the parent's LoRA instead of retraining (PROJECT_PLAN §C).
   - **Training progress detail** — currently just `training`/`done`/`error`. `TrainLoraNode`
     emits a `LOSS_MAP`; a loss/step readout is possible but not built.

**3. Poses/export polish** (already yours): FaceDetailer face-only alignment for the 28
   expressions (Track A learnings in root `workflows/README.md`), export naming edge cases.

---

## Key facts / gotchas to carry over

- **App ↔ ComfyUI is native HTTP**, workflows are templates + manifests (node ids never
  hardcoded in app code). Add params via the manifest.
- **Generation pattern:** submit non-blocking → store prompt_id → reconcile from
  `comfy.history_all()` on the next GET (see dataset/poses/train). Reuse it.
- **Training VRAM:** always free first (unload Ollama + `comfy.free_memory()`); `offloading:
  true` in the train graph. First train also loads Florence-2 (~extra VRAM + time).
- **ComfyUI list gotchas (from the caption R&D):** the dataset loaders output IMAGE as a
  *list*; `ImageListToImageBatch` folds it to a batch. `TrainLoraNode` accepts a per-image
  conditioning list matched to the batch. `AddTextPrefix` is `INPUT_IS_LIST` and *collapses*
  the list (breaks `CLIPTextEncode`) — use `StringConcatenate` (auto-maps).
  `SaveImageTextDataSetToFolder` writes to OUTPUT not INPUT, so inline captioning is the path.
- **Versioning interleave ends here:** LoRA was `0.5.x`, Poses `0.6.x`. Now one stream —
  continue on `0.6.x` (Phase 6) and treat LoRA-into-poses as Phase-6 work.
- **Logging:** log to the level convention — verbose = handshakes/share-copies/polls, info =
  milestones, warn = recoverable, error = failures (see `docs/ai-context.md`).
- **Test artifacts left in ComfyUI** (harmless, user can delete): input `pf-uploadtest`,
  `pf-traintest`; output `pf-traintest/captest*` LoRAs.
