# v0.7.2 — Pose & framing variety in the Dataset Builder

Phase 7. The highest-leverage quality fix left in the pipeline — the fix for weak,
pose-locked LoRAs.

## Why
A LoRA only learns identity *independent of stance* if it sees the character in many
framings and poses. The Dataset Builder used to vary only the **seed**, so every candidate
was the same waist-up stance — the LoRA overfit that one pose and then fought pose prompts
(the `sweetie-pie` character couldn't walk, wave, or cross its arms). This release makes the
dataset diverse **by construction**.

## Changed
- **Dataset batches spread candidates across a range of poses and framings** (full body,
  sitting, walking, arms crossed, three-quarter, side profile, low angle, back view,
  portrait…) *and* a fresh seed each. Cherry-pick 20 of these and the training set now has
  real pose diversity, so the trained LoRA can do the starter poses. The pose is injected via
  the base-character `expression` suffix — the same lever the Poses tab already uses — so
  there's **no new workflow or graph change**.
- **The rotation continues across batches:** *Generate 30* → *+10 more* keeps cycling from
  where it left off, so coverage stays even instead of restarting at the first pose.

## Added
- **"Pose & framing variety" toggle** on the Dataset tab (on by default). Uncheck for a
  same-pose, seed-only batch when you want one deliberate stance. New `pose_variety` field on
  `POST /api/projects/{id}/dataset/generate` (defaults to `true`).

## Pairs with the recipe
Combine a pose-varied dataset with **~1500–2500 training steps** for a usable, flexible LoRA.
This change plugs straight into the overnight `lora_build` job.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.2`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
