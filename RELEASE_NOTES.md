# v0.8.9 — Defaults that obey the skeleton, and three ways to hold your arms out

## The defaults were the problem

`strength 0.7 / end 0.7` was too weak to overrule a strength-1.0 character LoRA. Same seed,
same skeleton, only those two numbers changed: at 0.7/0.7 the pose was ignored, at 1.0/0.9
it was obeyed. The new defaults are **1.0 / 0.9**.

End is held short of 1.0 deliberately — the last steps free of the skeleton let the
character LoRA settle identity, and stop the skeleton's black background bleeding into the
frame (which is exactly what happened at 0.9 with a mismatched ControlNet model).

**Your existing personas are migrated too.** A column default only applies to new rows, so
without that this release would have looked like it did nothing. Personas still sitting on
the exact old 0.7/0.7 pair are lifted; if you'd tuned those numbers yourself, your values
are kept.

## You can now see which skeleton each pose uses

Every pose card shows the figure it renders from, so you can scan a set for the odd one out
instead of opening each pose:

- **`arms flung wide`** — set on this pose
- **`↳ hugging knees`** (italic) — inherited from the persona default
- **`no skeleton`** (amber) — renders from the prompt alone

## Three arms-wide variants

Exactly the three you described:

| Entry | Shape | Reads as |
|---|---|---|
| **arms wide, palms forward** | elbows dropped, forearms vertical | warding off, defensive |
| **arms wide, palms up and out** | arms low and open to the sides | helpless, resigned |
| **arms wide, palms inward** | elbows flung wide, forearms angled back in | exasperated, frustrated |

**One thing worth knowing:** COCO-18 has no hand joint and no wrist rotation, so a skeleton
*cannot* encode which way a palm faces. What actually differs between these three is the
forearm angle — and that's a real, visible difference in silhouette. The palm direction, and
the emotion riding on it, travel in each entry's `prompt_hint`. That's why they're three
entries and not one slider: the joints carry the shape, the hint carries the rest.

To get them: Poses tab → **Restore starter poses**. That button now *tops up* the library by
name rather than replacing the built-ins, so it adds the three new entries and leaves
everything else — including any edits you've made — untouched.

## Upgrade notes

Automatic. The migration runs at boot; no compose change.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.9`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
