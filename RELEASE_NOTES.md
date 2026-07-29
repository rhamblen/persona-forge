# v0.8.5 — The pose library

0.8.4 could drive a pose from *one* skeleton. This adds the set — **15 full-body starter
poses**, pickable for a whole persona or for a single pose.

| | |
|---|---|
| **Standing** (10) | neutral · weight on one hip · arms crossed · hands on hips · arms overhead · shrugging · head down · covering face · fists clenched · arms flung wide |
| **Grounded** (5) | kneeling upright · kneeling slumped · sitting legs to one side · hugging knees · lying down |

These map onto the emotion tiers you already have: `despair` wants the slumped kneel,
`elation` wants arms flung wide, `humiliation` wants covering face, `pride` wants hands on
hips.

## How to use it

Poses tab → *Pose structure & face pass* → **Choose skeleton…** for the whole set, or open a
pose and hit **Skeleton…** to override just that one. Thumbnails are rendered live from the
stored keypoints, so what you see in the picker is the actual data.

## Two things each entry carries

**A prompt hint**, appended automatically. A skeleton encodes a grip but not the sword — and
a front-view kneeling skeleton honestly looks like a short standing figure. The hint resolves
both. It's why kneeling works at all.

**A face-visible flag.** Head down, covering face and the slumped kneel switch the face pass
**off by default** — FaceDetailer can't find a face that isn't there, and forcing it repaints
a hand into something mangled. Your explicit per-pose setting still wins.

## Two things measured along the way

**Automatic pose extraction doesn't work for the poses that need it.** I generated five
grounded poses and ran DWPose over them: **every one returned "no person detected"**, under
two different detectors, including a clean hugging-knees figure. The only pose it detected
was a standing one. DWPose is trained on photographic upright humans. So the library is
hand-authored keypoints by design, not as a stopgap — importing from an image stays useful
for standing references only.

**A standing-only character LoRA overpowers the skeleton.** A kneeling skeleton at strength
0.7 rendered a *standing* figure with the character LoRA loaded, and kneeled correctly at the
same 0.7 with it removed — checked against the submitted graph, so the wiring was right. A
LoRA that has never seen its character kneel fights any skeleton that isn't standing, and at
moderate strength it wins.

That is the case for the next stage in one measurement. **Since you're rebuilding the LoRA
anyway, this is the moment**: stage H3c puts ControlNet into the *dataset build*, so the LoRA
learns the character kneeling and sitting rather than only standing. Teach it first, then
pose it — otherwise the only lever is raising ControlNet strength until identity suffers.

## Coming next

- **H3c — ControlNet in the dataset build.** The fix for the above, and the thing that makes
  every other pose stage work properly.
- **H3g — Blender as the skeleton source** *(new, your suggestion)*. Pose an armature, read
  the bone positions, project to COCO-18. No render and no detector, so the headless
  limitation on UR1 doesn't bite — and one 3D pose yields many skeletons from different
  camera angles, which is variety no hand-authored set can match. Written up in
  [`docs/pose-control.md`](docs/pose-control.md) §6.2.

## Upgrade notes

Automatic. The library seeds itself on first read; existing poses are untouched until you
pick a skeleton. No compose change.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.5`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
