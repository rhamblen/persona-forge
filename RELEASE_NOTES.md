# v0.7.10 — Sampler controls in the Prompt Studio

Phase 7. An Automatic1111-style set of sampler controls for the preview render, so you can dial in
refinement without editing the workflow.

## Added
- **"Generation settings" panel** under the Studio form — a collapsible block with **Steps**,
  **CFG**, **Sampler**, and **Scheduler**. These are the same knobs A1111 puts front-and-centre
  (*Steps* / *CFG scale* / *Sampling method* / *Schedule type*). It starts collapsed and defaults to
  the workflow's own values (28 / 5.0 / `euler_ancestral` / `normal`), so nothing changes until you
  open it and reach for a control.
- A one-line tip in the panel: `euler_ancestral` peaks around 28 steps, and pairing `dpmpp_2m` with
  `karras` is the combo that actually rewards higher step counts with sharper detail.

## How it works
- Purely a **frontend** addition. The `base-character` and `base-character-lora` manifests already
  declared `steps` / `cfg` / `sampler` / `scheduler`, and `POST /generate` already forwarded any
  workflow params — this release just adds the UI controls that send them.
- The settings are **ephemeral**: they apply to the **current preview run only** and are kept out of
  `formValues()`, so they never touch the saved prompt version, the version diff, or rollback. No
  database or schema change.

## Not yet verified
- The controls are wired end-to-end in code but haven't been exercised against a live ComfyUI run.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.10`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
