# UI style references

Design direction for the Persona Forge frontend, taken from the user's own work
rather than a generic default.

## The reference: `esp32-shutter-hub`

"Shutter Hub" is the user's own repo — [rhamblen/esp32-shutter-hub](https://github.com/rhamblen/esp32-shutter-hub),
specifically `ha-card/shutter-hub-card.js`. (Not the photography site of the same
name, which is what a web search finds.)

## Design spine (user directives, 2026-07-23)

1. **Version view like a VCS / code-history panel.** The user specifically likes the
   "version view of the code I'm using". This is the model for the **prompt rollback
   UI**: a timeline of versions, the signed-off baseline pinned, and a clear diff of
   what each edit changed. A headline feature, not a side panel.
2. **Left-hand menu.** Primary navigation is a left sidebar; content to the right.
3. **General template style** — colour, shape and feel per the tokens below.

## Design tokens extracted from the shutter-hub card

| Aspect | Value |
|---|---|
| Corner radius | **10px** on tiles, buttons and panels |
| Borders | **1px**, subtle divider colour |
| Fill | secondary/raised background, not pure page background |
| **Selection** | **accent border + `box-shadow: 0 0 0 1px accent inset`** — a ring, not a fill |
| Hover | border switches to accent |
| Buttons | **icon above a short label**, arranged in `auto-fit` grids |
| Secondary text | **11–12.5px**, muted colour |
| De-emphasis | `opacity: .45` rather than hiding |
| Theming | **CSS variables** throughout — no hardcoded colours |
| Density | compact and functional |

## How this is applied

- `.seg-tile` in `style.css` implements the tile + selection idiom and is shared by
  the **Logs filters** now; the **dataset picker in phase 4** (multi-select grid of
  candidate images) should reuse it, since that is the closest analogue to the
  card's tile grid.
- Panels/cards already use 10px radius + 1px borders + raised fill.
- Everything is driven by the `:root` variables so a palette swap is one edit.

## Still open

- The **palette** is currently a dark theme of my choosing. The shutter-hub card
  inherits Home Assistant's theme variables, so it has no fixed palette to copy.
  A screenshot of the look the user wants (or a set of hex values) would settle it.
- Light-mode support: not implemented; the variables make it straightforward.
