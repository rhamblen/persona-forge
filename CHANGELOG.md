# Changelog

Versioning is `0.<phase>.<iteration>` — the middle digit is the project phase, the
last increments with each update inside that phase. `1.0.0` will be the first
complete release.

Every version below is a **published GitHub Release** with a matching
`ghcr.io/rhamblen/persona-forge` image tag. Nothing is parked under "Unreleased".

---

## [0.2.4] — 2026-07-24

**Phase 2 — Logs, and a cross-container permissions fix.**

### Added
- **Clone a persona** (`POST /api/projects/{id}/clone` + sidebar button). Copies the
  current prompt into a new persona so it can be varied — the same character
  *skiing* and *lazing on a beach*. Identity (`character`) is kept, `style` is
  editable at clone time, and `parent_project_id` is recorded so **Phase C can offer
  to reuse the parent's LoRA instead of retraining** — turning an outfit/scene
  variant from a ~1 hr training job into a prompt change.
- Personas persist and reload from the sidebar selector, with their full version
  history intact.
- **Logs tab** — a first-class view, filterable by level (`debug`/`info`/`warn`/
  `error`) and category:
  - `boot` — startup: config, db init, builds-mount check, workflow manifest
    validation
  - `integration` — ComfyUI calls: submissions, queueing, completion, failures
  - `process` — pipeline steps: project created, version saved/signed off/rolled
    back, generation start → finish
  - `local` — folder creation, ownership changes, file work
- Records go to **stdout** (`docker logs persona-forge`), an **in-memory ring** the
  UI polls, and a **rolling JSONL** in `appdata/logs/` so boot history survives a
  restart. "Load previous runs" reads that file.
- `GET /api/logs` (filters + stats) and `GET /api/logs/persisted`.
- `POST /api/projects/{id}/repair-permissions` — re-applies ComfyUI-writable
  ownership to a build folder created before this release.

### Fixed
- **Generation failed with `Permission denied` on the shared builds folder.**
  Persona Forge runs as root and created `<build>/images/` as `root:root`, which
  the ComfyUI container (a different user) could not write into. Build folders are
  now chowned to `PUID:PGID` (default `99:100`, Unraid `nobody:users`) and chmodded
  `775`, with a `0777` fallback if chown isn't permitted.

### Changed
- `PUID` / `PGID` are configurable in `docker/.env`.

---

## [0.2.3] — 2026-07-24

**Phase 2 — Prompt Studio UI.**

### Added
- Prompt Studio: project create/select, character / style / negative editor,
  checkpoint picker populated live from ComfyUI, seed with reroll, Generate with
  inline preview.
- **Version history as a VCS-style rail** — a node per version, diff tags showing
  which fields changed, `signed off` / `current` chips, and per-version *Roll back*
  and *Sign off* actions.
- Sign-off captures unsaved edits first, so the baseline always matches what is on
  screen.
- Unraid `net.unraid.docker.webui` / `icon` labels so the container gets a clickable
  WebUI link.

### Fixed
- `frontend/` and `VERSION` resolved differently in a repo checkout vs. the
  container, so the root route 404'd locally. All asset paths now go through one
  resolver.

---

## [0.2.2] — 2026-07-23

**Deployment corrected to the project convention.**

### Changed
- The stack now **pulls a prebuilt image from GHCR** instead of building from
  source, so **only `docker/` is copied to UR1** — no application source on the
  server. This matches how `blender-mcp` and `comfyui-mcp` deploy.
- Added `.github/workflows/publish-image.yml` (publishes on `v*` tag or manual run).
- `docker/docker-compose.build.yml` keeps source builds available for local dev only.

---

## [0.2.1] — 2026-07-23

### Changed
- Moved `docker-compose.yml` and `.env.example` into a **`docker/`** folder to match
  the established project layout.
- Corrected the shared builds path after the folder was renamed.

---

## [0.2.0] — 2026-07-23

**Phase 2 — backend foundations.**

### Added
- SQLite store where `prompt_versions` is **append-only**: an edit inserts a child
  row and moves a `current` pointer, so rollback is safe and a signed-off prompt
  cannot be lost.
- ComfyUI HTTP client (submit / poll / outputs / view; live model lists from
  `/object_info`).
- **Workflow templates + parameter manifests**, so node IDs aren't hardcoded in
  application code, plus `validate_manifest()` to catch drift after a workflow edit.
- First template: `base-character`.
- API: project create (makes `<builds-root>/<slug>/{lora,images}`), version
  create / sign-off / rollback, generate, image proxy, model lists.

---

## [0.1.1] — 2026-07-23

### Fixed
- Corrected the shared builds host path.

### Added
- Project rationale in the README and plan.

---

## [0.1.0] — 2026-07-23

**Phase 1 — skeleton and deploy loop.**

### Added
- FastAPI backend with health, ComfyUI status, and a **storage check that actually
  write-probes** the shared builds mount — the dependency everything else rests on.
- Static frontend shell: left sidebar with ComfyUI and Builds status pinned at the
  top.
- `docker-compose` stack, `.env.example`, and the `appdata/` layout.
- MIT licence.

[0.2.4]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.4
[0.2.3]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.3
[0.2.2]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.2
[0.2.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.1
[0.2.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.0
[0.1.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.1
[0.1.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.0
