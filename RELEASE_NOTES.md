# v0.8.6 — Your dataset is visible when ComfyUI is stopped

You spotted this: the dataset lives on a shared folder, so why does it disappear when
ComfyUI isn't running?

It didn't. The **files** were always on the share and the **rows** were always in the
database — what vanished was the *thumbnails*. Every image on every screen was requested
through `GET /api/image`, and that endpoint proxied straight to ComfyUI's `/view` endpoint.
ComfyUI down meant no image bytes, so the Dataset grid loaded its candidates and your
selections and then rendered a wall of broken images.

## The fix

Persona Forge mounts the **same host folder** ComfyUI writes into — both containers bind it
as `/builds`. So `/api/image` now reads the file off that mount directly, and only asks
ComfyUI for images that genuinely aren't there (`type=input`/`temp` live in ComfyUI's own
directories, not on the share).

Working with ComfyUI stopped, restarting, or busy:

- **Dataset** — browse candidates, select and deselect, retarget, purge, delete
- **Poses** — the pose grid and every rendered pose
- **Expression sheets** and **Studio previews**

Generating still needs ComfyUI, of course. But reviewing and curating what you already have
no longer does — which is most of the time you spend in the Dataset tab.

One smaller thing: when the fallback *is* used and ComfyUI is unreachable, you now get a
clear **503 "ComfyUI is unreachable"** instead of an unexplained server error.

## Upgrade notes

Automatic — no compose change, no database migration. Your existing `/builds` mapping is
already what this uses.

If a thumbnail still 404s after upgrading, that image genuinely isn't on the share (deleted
from disk, or generated before the shared mount existed) — the DB row outlived the file.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.6`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
