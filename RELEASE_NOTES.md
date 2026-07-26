# v0.8.0 — Concept LoRA stack

**Phase H begins.** The first piece of the emotional-depth work described in
[`docs/emotion-depth.md`](docs/emotion-depth.md): a way to overlay third-party pose/gesture LoRAs
on top of your own character LoRA.

## Why this first

This pipeline uses two very different kinds of LoRA, and until now only knew about one:

|  | **Character LoRA** | **Concept LoRA** |
|---|---|---|
| Source | trained here, per persona | third-party, reused everywhere |
| Carries | *who* — identity | *what the body is doing* |
| Count | exactly one | zero to a few, stacked |
| Weight | ~0.8–1.0 | ~0.4–0.8 |

Stacking a concept LoRA lets you reach poses your character LoRA can't produce alone. It also
sets up the enrichment loop that follows: the **teacher LoRA** pattern — stack one to *generate*
training shots the character LoRA couldn't make, curate them, retrain, then drop the concept LoRA
at render time. That turns dataset enrichment from "recycle my own output" into "absorb a
capability", which is what makes the rest of Phase H safe.

It is useful on its own, today, with no enrichment involved: better poses in every render.

## Added
- **Concept LoRA stack** in the Prompt Studio — stack N LoRAs on top of the style/character LoRA,
  with per-entry enable, strength, reorder (the chain applies top-down) and remove. It is **saved
  on the prompt version**, so it rolls back with the prompt and appears in the version diff.
- **Concept LoRA library** with a "Manage library…" modal — register a LoRA once, stack it on any
  persona. Each entry records **base-model compatibility** (an SD1.5 LoRA will not load on an SDXL
  checkpoint — this is compatibility, not a label), **trigger words**, a recommended weight range,
  and a category (pose / gesture / expression / style).
- **Automatic trigger words** — enabled entries' triggers are appended to the prompt and
  de-duplicated. Most concept LoRAs do nothing without them.
- **Clear failure when a file goes missing** — a stacked LoRA ComfyUI can't see is reported by
  name before anything is queued, rather than dying inside ComfyUI with a node error.

## How it works
- `build_graph()` splices a **chain of core `LoraLoader` nodes** — one per entry, model+CLIP
  threaded through in order, every downstream consumer repointed at the tail. **Core nodes only**:
  a power-loader custom node would be tidier, but `custom_nodes` is read-only over SMB on UR1, so
  this needs nothing installed.
- Two manifest shapes: `base-character-lora` reuses its existing loader as the chain anchor;
  `pose-with-lora` has the whole chain injected (its character LoRA is model-only, with CLIP
  straight off the checkpoint), so no second workflow file was needed and an empty stack leaves
  the graph untouched.
- The stack applies to **Studio previews, dataset batches, and pose renders**. Dataset batches are
  the interesting one — a pose LoRA is exactly how a dataset gains body-language variety.

## Upgrade notes
- **Automatic migration.** `prompt_versions.lora_stack_json` (default `[]`) and a new
  `concept_loras` table are created on boot. Existing versions are untouched and render exactly as
  before — the stack is opt-in and starts empty.
- Stacks store **filenames, not library ids**, so removing a library entry can never break a saved
  version; the entry shows as `unregistered` and can be removed from the stack.
- **Keep stacks to 2–3.** Stacked LoRAs fight each other and identity is what loses. New entries
  default to the middle of the library's recommended weight range, not 1.0.

## Verified
Graph construction (both chain shapes, plus the single/empty/legacy paths), library CRUD and its
validation, version save / inherit / rollback of the stack, the missing-file guard, and every
stack interaction driven in a browser against a live ComfyUI 0.28.0. No image was generated as
part of this verification — the render itself is unchanged apart from the extra loaders.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.0`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
