# v0.8.12 — The dataset is posed

## The measurement this fixes

2026-07-28, on the live box: a kneeling skeleton, correctly staged and applied at strength
0.7, rendered a **standing** figure with the character LoRA loaded — and knelt correctly with
the LoRA removed.

That is not a ControlNet bug. A LoRA trained only on standing shots has no idea what this
character looks like kneeling, so the skeleton forces geometry the LoRA has never seen, the
two fight, and the result is melted anatomy. No amount of render-time tuning fixes it.

**Teach the body first, then pose it.** That's this release.

## What's in it

Every *other* full-body dataset candidate now renders against a skeleton from the pose
library. Close-ups are never posed — they exist to teach identity at high frequency, and a
skeleton over a face crop constrains nothing.

Skeletons are walked **round-robin across posture families** — standing, crouching, kneeling,
sitting, lying — rather than in id or name order. The shipped catalogue is standing-heavy (13
of its 24 entries), so walking it in order would have handed a 30-image batch mostly standing
figures, which is the exact gap this closes. The rotation continues across batches, the way
framing rotation already did.

On the Dataset tab: a **Posed body shots** toggle and a strength dial. Both preconditions —
a ControlNet model and a non-empty pose library — are set on the Poses tab, so the hint names
whichever one is missing rather than letting Generate fail after you've chosen a count.

It needed **no new workflow file**. ControlNet touches conditioning, LoRAs touch model/CLIP,
so the existing splice drops into `base-character` and `base-character-lora` untouched and
composes with the concept-LoRA chain.

## Four calls worth knowing about

**Half the body shots, not all of them.** The un-posed half keeps the spread of views and
camera angles that the largely front-facing library can't supply yet. Posing everything would
have traded view variety for posture variety instead of gaining both.

**Its own strength — 0.6/0.7, against the sprite path's 1.0/0.9.** The two uses want opposite
things. A sprite render wants the skeleton obeyed; a dataset build wants posture variety the
checkpoint still finishes naturally. At 1.0 the dataset becomes a set of stiff mannequins and
the LoRA learns the stick figure's habits rather than the character's body.

**Stance words are dropped from a posed candidate's prompt.** 0.8.9 measured that stripping
stance words from an *agreeing* prompt didn't help — that still holds. This is the other case:
"walking forward, mid-stride" against a kneeling skeleton is an outright contradiction.

**A misconfigured posed batch is refused, not rendered.** 0.8.8 taught this on a single sprite
(skeleton set, no model → renders fine, ignores the figure). Here the silent version costs ~30
renders and an hour of GPU, and the output *looks* correct — it just teaches the LoRA nothing.

## What's verified, and what isn't

Verified offline against the real templates: the splice adds exactly three nodes, every
consumer of the raw conditioning is repointed, the dataset strength and end reach the apply
node, ControlNet composes with the LoRA chain, union models get `SetUnionControlNetType`, and
turning it off leaves the graph byte-identical. Candidate maths: posed shots are always body
shots, each draws a distinct skeleton, batches continue rather than restart, and no posed
prompt carries a stance word.

**No image has been rendered yet.** The render-and-look loop is unchanged: build a batch and
read the contact sheet. The thing to watch for is stiffness — if the bodies read as posed
mannequins rather than a character who happens to be standing that way, lower the strength
before changing anything else.

## Upgrade notes

Automatic. Three nullable columns are added to `projects` on boot; posed shots are **off by
default**, so an existing persona's next batch behaves exactly as it did before until you turn
it on. No compose change.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.12`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
