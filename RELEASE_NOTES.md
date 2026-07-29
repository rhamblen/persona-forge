# v0.8.8 — A skeleton with no ControlNet model can't fail silently any more

Re-rendering with a different pose figure looked like it failed. It didn't fail — it
**succeeded and ignored the figure**, which is worse, because nothing anywhere said so.

## Why the logs were clean

Your persona has **no ControlNet model selected**. Pose rendering only applies structural
control when it has *both* a skeleton and a model; with one missing it quietly renders from
the prompt alone. So the batch ran, all 28 poses came back `done`, zero errors, zero
warnings — and every skeleton you picked was discarded on the way through.

The skeleton picker made it worse by confirming "Skeleton set. Regenerate poses to use it."
That sentence was not true.

## What changed

- **The picker tells you now.** Choosing a skeleton with no model selected says: *Skeleton
  set (Sitting — hugging knees) — but no ControlNet model is selected, so this skeleton will
  NOT be used.*
- **The log says it too**, at `warn`, naming the pose and skeleton — so this is findable in
  the log rather than only in the output.
- **The panel summary stops hiding it.** "not configured — poses render from the prompt
  alone" described both *nothing set at all* and *skeleton set but inert*. The second now
  reads **"skeleton set but NO ControlNet model — renders ignore it; pick a model below"**,
  in the warning colour.

## To actually get your pose figures working

Poses tab → **Pose structure & face pass** → pick a ControlNet model. Both of these are
installed and available on your ComfyUI:

| Model | File |
|---|---|
| NoobAI openpose (native) | `noobai-openpose-sdxl.safetensors` |
| xinsir openpose SDXL 1.0 | `xinsir-openpose-sdxl-1.0.safetensors` |

The NoobAI one matches your checkpoint (NoobAI-XL), so start there. Then set your skeletons
and regenerate — this time the figures will be obeyed.

Nothing selects a model for you: with 24 ControlNets visible to ComfyUI, guessing wrong
produces confidently mangled anatomy, so the choice stays yours.

## Upgrade notes

Automatic — no compose change, no migration. Not a regression; this dates from H3a and
needed exactly your configuration to surface.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.8`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
