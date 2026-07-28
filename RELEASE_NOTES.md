# v0.8.4 — Structural pose control

Poses in a set came out looking like each other. There were **three separable causes**, and it
was worth pulling them apart before building anything:

1. **Every pose rendered at the same seed.** Same prompt, same LoRA, same seed, differing only
   by a short expression suffix — diffusion is deterministic in the seed, so a near-identical
   picture is the *expected* result, not a mystery.
2. **Posture prose is weakly obeyed.** The checkpoint spends its budget on subject and style,
   and a character LoRA pulls hard toward the stance its dataset over-represented.
3. **Nothing constrained where the limbs went.** The prompt was the only lever on posture, and
   it is the wrong kind of lever.

This release fixes 1 and 3. Fixing 3 makes 2 stop mattering.

## Three layers, two passes

A pose is now three independently tunable layers:

| Layer | What it sets | Mechanically | Re-tuning costs |
|---|---|---|---|
| **base** | prompt, character LoRA, seed | pass 1 | full re-render |
| **body** | the skeleton, ControlNet strength and reach | conditioning on pass 1 | full re-render |
| **face** | the emotion, and how hard it's pushed | pass 2 | **face pass only** |

That asymmetry is the point. The face is the layer most likely to need another try — it carries
the emotion, and its denoise is the dial nobody gets right first time — and it is the only one
that can be re-run **without losing a body you already liked**. So the pass-1 image is kept, and
**"Re-roll face" takes 14 seconds against ~104 for a full re-render**, with the pose held fixed.

## Calibrated, not guessed

Face-pass denoise defaults to **0.60** because that was measured on your box, not chosen from a
range:

- **0.45** — barely moves an expression. Not a useful default.
- **0.60** — a genuinely furious, shouting face, identity intact.
- **0.75** — the jaw disintegrates. Past the cliff.

That independently reproduces the threshold the Track A expression workflow arrived at the hard
way, which is about as much confidence as a setting like this gets.

## Two things worth knowing before you run it

**ControlNet beats posture prose outright.** A neutral standing skeleton, with a prompt asking
for *"furious, shouting, leaning forward aggressively, one fist clenched and raised"*, renders a
calm woman standing at rest. The skeleton wins. This is why the next stage binds a *different*
skeleton per emotion tier — it isn't a convenience, it's the only way an emotion's body happens.

**The face pass needs an anime-capable checkpoint.** On base SDXL the face comes out flat at
every denoise setting. Confirmed as a 2×2 over {base SDXL, NoobAI} × {no character LoRA, LoRA at
1.0}: the **checkpoint** is the deciding variable, and the character LoRA emotes perfectly well
at full strength — it is not the limiting factor. The Poses tab now warns when a persona is
pinned to a base checkpoint, because it presents as a broken feature rather than a wrong model.

*(`sweetie pie` is currently on `!first/sd_xl_base_1.0` and will render flat faces until it
moves to NoobAI-XL.)*

## Setup

Two OpenPose ControlNets are already installed on UR1 and **register themselves** on first use:

| File | For |
|---|---|
| `noobai-openpose-sdxl.safetensors` | NoobAI-XL / Illustrious — first choice |
| `xinsir-openpose-sdxl-1.0.safetensors` | any other SDXL checkpoint |

On the **Poses** tab, open *Pose structure & face pass*: pick a ControlNet, click **Use built-in
standing skeleton**, and Apply. Poses render prompt-only until both a ControlNet *and* a skeleton
are set, so nothing changes until you opt in.

## Coming next (stage H3b/H3c)

A **pose library** — many skeletons, bound per emotion tier, so `despair` gets a slumped kneel
and `elation` gets arms flung wide — plus ControlNet in the **dataset build**, which is what
teaches the LoRA what this character's body does at all.

## Upgrade notes

Schema migration is automatic (seven nullable columns on `poses`, seven on `projects`, and the
`controlnets` table). Existing poses render exactly as before until you opt in. **No compose
change**, but the image gains a dependency (Pillow), so pull rather than reuse a cached layer.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.4`

Full detail in [`CHANGELOG.md`](CHANGELOG.md); design and measurements in
[`docs/pose-control.md`](docs/pose-control.md).
