# v0.8.3 — Admin tools

Everything in Persona Forge could be created and nothing could be removed. A scrapped
experiment stayed in the picker forever, and a bad LoRA sat next to the good one with only its
timestamp to tell them apart. This release adds the missing half: **delete a persona, a prompt
version, or a trained LoRA.**

## Deliberate, not general

The version store is append-only on purpose, and that stays true. The append-only rule exists to
prevent **accidental** loss — so deletion is a *deliberate* action with guards, never something
that happens on its own:

- The **current version can't be deleted** — roll back to another one first.
- A persona always keeps **at least one version**.
- A **signed-off baseline** takes a second confirmation that names it as the approved reference.
- A persona with a **running build** refuses to delete — cancel the build first, rather than
  having its job row vanish from under the worker mid-stage.
- **Clones are kept.** Deleting a parent persona orphans its clones (they lose the parent link);
  it never cascades into personas you didn't ask to remove.

## Delete a persona

**🗑 Delete this persona** in the sidebar. The confirmation names the persona, counts its
versions and poses, and shows the exact build folder path.

Ticking **"also delete the build folder"** is a separate decision, and deliberately so: that
folder holds every rendered image and the trained LoRA — often an hour of GPU time. Leave it
unticked and the persona leaves Persona Forge while the files stay on the share.

## Delete a prompt version

A **Delete** button on any version that isn't the current one. Its children **re-attach to its
parent**, so pruning a mid-history version leaves the chain connected rather than fragmenting it
into orphans. Images generated from that version are kept — they just lose the link.

## Delete a trained LoRA

A ✕ on each file in the LoRA list. If you delete the one currently selected for pose renders,
the selection is **cleared and the UI tells you** — otherwise poses would keep asking ComfyUI for
a file that no longer exists.

## Notes
- Every deletion is logged with what went and what it cost: version/image/pose counts, whether
  the build folder was removed, and how many clones were orphaned.
- LoRA filenames are treated as path components only, and build-folder removal refuses any path
  that isn't a direct child of the builds root — a blank or odd slug can never turn into "delete
  the builds root".
- Verified in a browser end to end, including both cancel paths, the signed-off double-confirm,
  re-parenting checked in the database, the running-build refusal, and delete-with-files versus
  delete-without-files.

## Upgrade notes
Automatic. No schema change, no compose change.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.3`

## Upgrading
No compose changes. Pull and restart:

```bash
docker compose pull && docker compose up -d
```
