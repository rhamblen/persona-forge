# v0.7.3 — Close-ups + varied expressions in the dataset

Phase 7. The other half of the weak-LoRA fix. 0.7.2 made the dataset vary **pose**; this
adds the two things that were still making trained LoRAs *weak*: **close-up framings** and
**varied facial expressions**.

## Why
A LoRA learns face fidelity from face *pixels*. An all-full-body training set means the face
is a tiny patch in every frame → a blurry, weak identity. And if every image wears one
expression, that expression glues itself into identity and fights you when you later prompt
`angry` or `sad` (the "smile leaks into grief" failure). Both are fixed by making the dataset
diverse on those axes.

## Changed
- **Framing distance.** ~**40%** of each batch is now **close-up / bust** (face fills the
  frame) for a strong face; the rest are full-body/pose shots for body, outfit and pose
  independence.
- **Facial expression.** Candidates cycle through **neutral, happy, sad, angry, shocked,
  embarrassed, alluring, flirtatious** (neutral-weighted). Because the trainer captions each
  image (Florence-2), the expression lands in the caption and **decouples** from the trigger
  word instead of binding to identity.
- The two axes rotate independently (12 framings × 10 expressions) → a batch of 30 gives
  **30 unique framing+expression pairs**, none repeated; *+10 more* continues the rotation.
  Same injection lever as before — **no new graph, no schema change.**
- Dataset-tab toggle relabelled **"Framing, pose & expression variety."** Uncheck for a
  same-framing, neutral, seed-only batch.

## Watch out for
If a project's **style** prompt hard-codes a framing like "full body," it can fight the
close-up candidates (the suffix usually wins, not always). The app won't rewrite your prose —
drop framing words from the style field if close-ups render wide.

## The full recipe now
Pose-varied **+** close-up-rich **+** expression-varied dataset **+ ~1500–2500 training steps**
= a strong, flexible LoRA. The dataset side is now complete; raising the training-step default
is the last lever.

**Image:** `ghcr.io/rhamblen/persona-forge:0.7.3`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
