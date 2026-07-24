# Changelog

Versioning is `0.<phase>.<iteration>` — the middle digit is the project phase, the
last increments with each update inside that phase. `1.0.0` will be the first
complete release.

Every version below is a **published GitHub Release** with a matching
`ghcr.io/rhamblen/persona-forge` image tag. Nothing is parked under "Unreleased".

---

## [0.3.0] — 2026-07-24

**Phase 3 opens: the AI prompt assistant.**

### Added
- **AI prompt assistant (Ollama).** A new card sits *above* the three manual prompt
  fields. Type a plain-language description or instruction, choose **Replace**
  (author all three fields fresh) or **Modify** (edit the current prompt), and hit
  **Suggest** — it fills character / style / negative in one shot. Nothing is saved
  automatically: the suggestion lands in the editable fields with a **reject-and-undo**
  link, and only becomes a version when you Save as usual.
  - Talks to Ollama over its native HTTP API (`/api/generate`, `format:json`), same
    "no JSON-RPC hop" reasoning as the ComfyUI client.
  - The system prompt enforces the project's settled rules: **prose, not Danbooru
    tags**, and **no expression/emotion/pose words in the character field** (a baked-in
    smile leaks into anger/grief). Verified live: "cheerful catgirl…" produced a clean
    identity with the mood kept out of `character`.
  - **Modify never destroys** a field the instruction didn't touch — if the model
    returns an empty field, the current value is kept.
  - Endpoints: `GET /api/ai/status` (reachability + model list, shown as a chip),
    `POST /api/ai/suggest-prompt`. Configurable via `OLLAMA_URL` / `OLLAMA_MODEL`
    (default `http://192.168.1.32:11434`, `llama3.1:latest`).
- **Ollama in the sidebar, with Connect / Unload.** The pinned connection block now
  shows Ollama alongside ComfyUI and Builds — `offline` / `idle` (reachable, model not
  in VRAM) / `loaded`. **Connect** preloads the model so the first suggestion is instant
  instead of a ~60s cold load; **Unload** frees VRAM immediately. Suggestions also carry
  a `keep_alive` (`OLLAMA_KEEP_ALIVE`, default 30m) so the model auto-unloads after a
  spell of no use rather than pinning VRAM on the shared, always-on box. Endpoints:
  `POST /api/ai/warm`, `POST /api/ai/unload`; status now reports `loaded`.
- **Start / restart ComfyUI and Ollama from the app.** Both run as containers on the
  same host (UR1) as Persona Forge, so the sidebar now offers **Start** (when a
  container is stopped) and **Restart** for each. A ComfyUI restart is refused while its
  queue is busy unless forced, so an in-flight generation isn't killed by accident.
  - Access goes through a **scoped `tecnativa/docker-socket-proxy` sidecar**, never the
    raw Docker socket. The proxy is limited to `CONTAINERS` (list/inspect),
    `ALLOW_START` and `ALLOW_RESTARTS` — it **cannot** create, remove or exec
    containers, nor touch images / volumes / networks. The real socket is mounted
    **read-only**, and the proxy sits on an `internal` network unreachable from the LAN.
  - Disabled by default-safe: unset `DOCKER_PROXY_URL` and the feature (and its UI)
    simply disappear. New endpoints: `GET /api/containers/status`,
    `POST /api/containers/{key}/start`, `POST /api/containers/{key}/restart?force=`.
  - Only recovers a *stopped container on a live host* — if UR1 is down, so is PF.
- **Preview zoom.** The generated preview is now click-to-zoom into a full-screen
  lightbox (click the backdrop or press Esc to collapse), plus an **open in new tab**
  link on the image caption.

### Fixed
- **New personas defaulted to a photoreal checkpoint.** The checkpoint dropdown was
  populated straight from ComfyUI, which returns models in folder order — so option
  0 was `!first/consistentFactor_euclidCinematicV61`, a cinematic photoreal model,
  and the first generate came out looking wrong for an anime persona. The default is
  now *resolved* rather than positional: exact match on `DEFAULT_CHECKPOINT`
  (`animi/NoobAI-XL-v1.1.safetensors`, the model the working 28-expression workflows
  use), else the first model matching `PREFERRED_CHECKPOINTS`
  (`NoobAI-XL`, `animi/`, `AnythingXL`), else position 0 with a warning logged.
  Both are env-overridable in `docker/.env`.
- `POST /api/projects` now resolves the default server-side when no checkpoint is
  given, so the initial prompt version records a real model instead of `''`.
- Projects created before this release stored an empty checkpoint and so inherited
  the same wrong option in the UI; the form now falls back to the resolved default,
  which fixes them without a db migration.

### Changed
- `GET /api/models` returns a `default` field alongside `models`.

---

## [0.2.7] — 2026-07-24

### Changed
- **`docker/.env` is now shipped as a real, tracked file** instead of
  `.env.example`. Copying `docker/` to the server now yields a working stack with
  no rename step. It contains only non-secret config (ComfyUI URL, host paths,
  port, `PUID`/`PGID`, `TZ`); `.gitignore` still ignores every other `.env`, with a
  single explicit exception for this one. If a credential is ever needed, it must
  move out of version control and the exception be dropped.

---

## [0.2.6] — 2026-07-24

**Consistency pass: docs, UI language, and self-describing builds.**

### Added
- **`persona.json` sidecar** written into each build folder (on create, clone,
  version change, sign-off and rollback). The sqlite db in `appdata/` remains the
  working store, but a build folder was not self-describing — copy it elsewhere or
  lose the db and you kept the images without the prompt that made them. Each build
  now carries its persona, current prompt, signed-off baselines and full version
  history.
- `docs/ui-style.md` now records the **actual design tokens** taken from the user's
  own `esp32-shutter-hub` HA card (10px radius, 1px borders, accent-ring selection,
  icon-above-label tiles, 11–12.5px muted secondary text, CSS-variable theming).

### Changed
- **Logs tab restyled for consistency** — the ad-hoc dropdown/checkbox toolbar is
  replaced with the shared `.seg-tile` idiom (accent border + inset ring for the
  selected state), matching the rest of the app and the shutter-hub card. Level
  badges are now chips; the row grid collapses on narrow screens.
- **README restructured** to match the conventions in `esp32-shutter-hub` and
  `pihole-mcp`: badges → intro → Why? → Features → Quick Start → Configuration →
  Repo layout → Status → Documentation → Related projects → License.
- **`## Status` no longer duplicates the changelog** — it is now a short statement
  of the current phase, pointing at `CHANGELOG.md` and `PROJECT_PLAN.md`.

### Fixed
- **Persistent state was created *inside* `docker/` instead of beside it, and was
  confusingly called `appdata`** (nested under `/mnt/user/appdata/` already). The
  compose file used a relative bind source, which docker compose resolves against
  its *project directory* — Unraid's Compose Manager does not reliably set that to
  the compose file's folder.

  Replaced with two explicit, **absolute, required** paths that sit as **peers of
  `docker/`**:

  ```
  /mnt/user/appdata/persona-forge/
  ├── docker/   compose + .env  (the only folder copied to the server)
  ├── db/       sqlite: personas, prompt history
  └── logs/     rolling log file
  ```

  Compose now fails fast with a clear message if `DB_HOST_PATH` or `LOGS_HOST_PATH`
  is unset. The `APPDATA_ROOT` / `APPDATA_HOST_PATH` concept is gone.

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

[0.2.7]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.7
[0.2.6]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.6
[0.2.4]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.4
[0.2.3]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.3
[0.2.2]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.2
[0.2.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.1
[0.2.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.2.0
[0.1.1]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.1
[0.1.0]: https://github.com/rhamblen/persona-forge/releases/tag/v0.1.0
