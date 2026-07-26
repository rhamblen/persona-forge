# v0.8.1 — Base-model neutrality

A small follow-up to [v0.8.0](https://github.com/rhamblen/persona-forge/releases/tag/v0.8.0).
The project isn't committed to a single checkpoint family, so nothing in the concept-LoRA work
should read as if it is.

## Changed
- The library's **Base model** field is relabelled — *"which checkpoint family it was trained
  for"* — with a broader placeholder (`sdxl, sd1.5, pony, illustrious`) instead of naming one
  model. It was always free text capable of holding entries for several families; the copy just
  didn't say so.
- Documentation and code comments no longer treat one checkpoint as the assumed target.

## No behaviour change
The 0.8.0 stack was already model-agnostic — `base_model` is free text per library entry and the
checkpoint is per prompt version, so nothing assumed a family. This release corrects wording, not
logic. Your library and any saved stacks are unaffected.

## Still open
**Concept-LoRA sourcing is deliberately deferred** rather than pinned to a checkpoint family. Once
the picture settles, the natural refinement is to surface or filter library entries against the
current version's checkpoint, instead of leaving compatibility to the eye. See the open decisions
in `PROJECT_PLAN.md` and `docs/emotion-depth.md`.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.1`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
