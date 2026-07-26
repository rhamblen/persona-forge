# v0.8.2 — The emotion map

**Phase H1a.** Emotion is two dimensions, not one: *which* emotion (**axis**) and *how much*
(**tier**). This release adds that structure — and makes it yours to change.

## The happy accident

SillyTavern's 28 expression labels are the GoEmotions set, and several already come graded:

- annoyance → **anger** → *fury*
- disappointment → **sadness** → **grief** → *despair*
- amusement → **joy** → **excitement** → *elation*

So grouping the 28 by axis gives you most of the intensity ladder for free. Only the top tiers
are new — 7 of them: **fury, terror, despair, elation, devotion, revulsion, humiliation**. The
28 stay the baseline and the export target; tiers are purely additive.

## It's a default, not a vocabulary

The shipped map is a starting point. Everything is editable, at every level:

- **Axes** — add your own (Exhaustion, Resolve, whatever your cast needs), rename, delete, and
  mark whether they're a real intensity ladder.
- **Tiers** — add, relabel, reorder along the ladder, delete, and rewrite the prose that drives
  the render.
- **Reset to default** whenever you want the shipped map back.

A stoic character doesn't need a rage tier; a monster may need axes no human has. Open
**Emotion map…** on the Poses tab.

## Added
- **Grouped poses grid** — poses now render grouped by axis with tiers in rising-intensity
  order, each group showing **done/total**. That's the real point: the baseline review has to
  answer *"which emotion is this persona weak at?"*, and an alphabetical grid of 28 can't.
- **"+ intensity tiers" preset** — the 28 plus the graded top tiers, in one click.
- Prose modifiers that describe **posture as well as face**. Rage and despair are body
  language; a face-only repaint can't render them, which is exactly why this pipeline trains a
  per-character LoRA.

## Notes
- **The map is authoritative.** Poses resolve against the current map by name, so renaming an
  axis or reordering a ladder re-groups the grid at once rather than showing whatever was true
  when the pose was created. A pose whose label has left the map keeps its last grouping.
- **`graded` is honest.** Anger and Sadness are ladders. Cognition ("confusion → surprise") is
  not — those axes are groupings, and they say so. Later enrichment will only offer "hone the
  intensity" where an intensity actually exists.
- **Custom tiers are flagged.** SillyTavern's classifier can never emit `fury` — custom labels
  need the planned state engine, or a manual trigger, to ever appear. Whether a label is one of
  ST's own 28 is a fixed external contract, tracked separately from your editable map.
- Deleting a tier or axis never touches rendered images — those poses just move to "Ungrouped".
- Tier labels must be unique: they become sprite filenames, and a clash would silently overwrite
  an export.

## Upgrade notes
Automatic. The map seeds on first boot, and existing poses are tagged with their axis by name.
An edited map is never overwritten by a later start.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.2`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
