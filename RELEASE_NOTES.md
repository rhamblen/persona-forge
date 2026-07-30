# v0.8.11 — Skeletons projected from a posed body, not typed by hand

## The case, in numbers

Of the 11 library entries authored by hand across 0.8.9 and 0.8.10, **five needed
re-authoring**. Every failure was the same kind: coordinates that are defensible as numbers
and wrong as a picture.

| Entry | What went wrong |
|---|---|
| `hugging knees, head buried` | head joints dropped → rendered headless |
| `legs stretched, ankles crossed` | standing leg proportions → read as standing |
| `legs stretched and apart` | same |
| `legs stretched, leaning back on hands` | same |
| `bending forward, arms out` | head above the neck → read as standing |

Projecting from a posed 3D body removes that class of error **by construction** — the body is
anatomically consistent because it came from a body.

## What's in this release

`project_bones()` takes world-space bone positions and returns 18 normalised COCO-18
keypoints, ready to drop straight into a library entry. It has **no Blender dependency**, so
it's testable offline and Blender's only job is handing over a dict of positions.

Bone names are matched loosely — Rigify (`forearm.L`), Mixamo (`mixamorig:LeftArm`) and plain
names all work. Unmatched bones are **dropped rather than guessed**: a wrong joint renders as
a broken body, an absent one renders as an occlusion, which OpenPose emits routinely anyway.

`describe()` gives the sanity figures worth reading before you save — height fraction, hip y,
whether the head landed below the neck, unmatched joints, anything off-canvas. It's not a
validator; an unusual pose can be perfectly correct.

## Two things it gets right that eyeballing didn't

**Foreshortening comes free.** Perspective projection, not orthographic, on purpose. Measured
on synthetic rigs: a seated figure with legs toward the camera projects a shin→ankle span of
**0.049** against a standing **0.237**. That's the exact error that cost three rewrites.

**Fixed scale, not per-pose fit.** The scale comes from a reference *standing* height, so a
crouch stays genuinely shorter than a stand — 0.438 of canvas height against 0.855.
Auto-fitting each pose would give every figure the same frame height and set SillyTavern
jittering as it swaps sprites. `ref_height`, `figure_fraction` and `footing` are library-wide
constants: keep them identical across every entry.

## Still to do

The **extraction** half — running the snippet in Blender to read a posed armature. It's
documented in [`docs/pose-control.md`](docs/pose-control.md) §6.2 and needs the BlenderMCP
addon's socket server running, which is a button in Blender's sidebar.

And the render-and-look loop stays mandatory. Projection lowers the error rate; it doesn't
remove the need to look at a contact sheet.

## Upgrade notes

Automatic — new module, no schema change, no compose change.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.11`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
