# v0.8.10 — Pose families: an emotion picks its posture, per character

This is the release where a sprite set stops being "one stance with a different face on it".

## Families

Every library entry now belongs to a posture family — **standing, crouching, kneeling,
sitting, lying** — and an emotion axis is assigned to a family. Crucially the assignment
works **per tier**, so a ladder can change posture as it climbs: annoyance and fury both
stand, but sorrow stands where despair sits on the floor.

Shipped defaults ground the top rung of `sadness` (sitting), `shame` and `fear` (kneeling),
and `affection` (crouching). Everything else stands. All of it is changeable.

## Per character, not per install

You said two characters shouldn't strike the same pose for the same emotion, so the map is
layered: global defaults underneath, a persona's own overrides on top, merged key by key. A
character inherits until you tell it otherwise, and changing one character never touches
another. Ayla's grief can lie down while Bruno's hugs his knees.

## Choosing within a family

A family has several members, and which one you get matters. The automatic spread is a name
hash — good for variety, **meaning-blind**, and quite capable of handing Elation "hugging
knees, head buried". So a tier can be **pinned** to a specific entry, and the hash is only
what happens when you haven't pinned one.

## Eight new skeletons

**Head position on the same body** — your observation, and it's the sharpest one in here:

| Entry | Reads as |
|---|---|
| hugging knees, **head buried** (face pass off) | despair, crying |
| hugging knees, **head up** | alert, eager |

Unlike palm facing, head tilt genuinely *is* in the skeleton: nose, eyes and ears are real
COCO-18 joints, so the vertical ordering carries the tilt (ears→eyes→nose = looking down).

**Sitting, legs extended** — hugging knees is a closed shape, useless for anything open:
`ankles crossed` (relaxed), `stretched and apart` (sprawled, unguarded), `leaning back on
hands` (at ease, confident).

**Crouching, for affection**: `squatting down`, `down on one knee, leaning in`, `bending
forward, arms out`.

Two of these were thrown away and re-authored: seated and crouching figures need
foreshortened legs *and* a compressed torso, or they render as standing figures no matter
how the legs are arranged. Contact sheets caught it; code review wouldn't have.

## API

`GET/POST /api/pose-families`, both taking an optional `project_id`. Resolution at render
time is: per-pose skeleton → persona default → persona family → global family → none. An
explicit per-pose choice always wins, so assigning a family can never quietly overwrite work
you did by hand.

## Upgrade notes

Automatic — migrations add the columns and backfill families from entry names. Hit **Restore
starter poses** to pull in the eight new entries.

There is **no UI for family assignment yet** — it's API-only this release. That's the next
piece of work.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.10`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
