# UI style references

Design direction for the Persona Forge frontend. The look should follow the
user's preferred references rather than a generic default.

## References provided by the user

- **Shutter Hub** (and the code editor / VCS tool the user works in) — cited for
  the overall template feel.

## Design spine (user directives, 2026-07-23)

Three things to borrow, in priority order:

1. **Version view like a VCS / code-history panel.** The user specifically likes
   the "version view of the code I'm using." This is the model for Persona Forge's
   **prompt rollback UI** — present prompt history the way a version-control tool
   shows commits/diffs: a timeline of versions, the signed-off baseline pinned, and
   a clear diff between versions (what an Ollama/manual edit changed). This is a
   headline feature, not a side panel.
2. **Left-hand menu.** Primary navigation is a **left sidebar** (phase/section nav),
   not a top nav bar. Main content area to the right.
3. **General template style — colour, UI shape, and feel to match the reference.**
   _Exact palette / corner-radius / density still to be pinned from a screenshot —
   see below._

<!-- Add screenshots to docs/ui-refs/ and link them here. Especially need one that
     shows the reference's colour palette, card/panel shape (radius, borders,
     shadows), and the version/history view the user likes. -->

## Still to pin (need a screenshot of the reference)

- Palette + light/dark (the "colour" in directive 3).
- Corner radius / borders / shadow — the "UI shape" (rounded & soft vs. crisp &
  square).
- Density — roomy/editorial vs. compact/tool-dense.
- The exact version-view layout to emulate (side-by-side diff? inline? timeline
  rail?).

## What we need to pin down before building the frontend (M1)

- Overall layout: sidebar + main canvas? top-nav + wizard steps?
- Density: roomy/editorial vs. compact/tool-dense.
- Palette + light/dark.
- Gallery card style (the dataset + pose grids are the visual centrepiece).
- Prompt editor + version-timeline presentation.
