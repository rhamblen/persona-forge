# persona-forge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/ghcr.io-persona--forge-2496ed.svg)](https://github.com/rhamblen/persona-forge/pkgs/container/persona-forge)
[![Python](https://img.shields.io/badge/python-3.12-brightgreen.svg)](https://www.python.org)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-required-purple.svg)](https://github.com/comfyanonymous/ComfyUI)

**Forge a whole SillyTavern character from a single description — the persona *and* the face.**

Persona Forge is a guided web app that grows one plain-language idea into a complete
character: the **written persona** SillyTavern role-plays (a character card — backstory,
personality, voice, quirks) *and* a **matching visual identity** — a trained per-character
LoRA and a full **expression + posture** sprite set. It runs on your own ComfyUI, with
**Ollama** to shape everything in plain language and **full rollback** so nothing you sign
off is ever lost.

## Why?

A SillyTavern character is really two things that usually get built separately: a
**persona** (who they are — backstory, personality, how they speak) and a **look** (how
they appear on screen). Build them apart and they drift — the art stops matching the
writing, and the writing forgets the art. Persona Forge treats them as **one build from
one description**: you describe the character once, and both the character card and the
visual set come out of that single source, staying consistent with each other.

The visual side doesn't stop at a static portrait — you want **expressions**. And facial
expressions alone are easy to misread or simply not notice, especially across 28 of them,
so Persona Forge varies **posture** as well, which makes the character far more readable.
(Live2D and VRM can do this too, but they're complicated to build; this stays 2D sprites.)

Posture is also why this trains a LoRA rather than just prompting: varying the body means
re-generating it, and only a per-character LoRA keeps it recognisably the same person.
(IPAdapter alone was tested and drifts — hair, outfit and proportions wander shot to shot.)

**The whole build, from one idea:**

```
one-line concept
   │  Ollama drafts a character sheet (persona + looks), you refine it
   ▼
Character Studio ──► SillyTavern character card   (the persona ST chats with)
   │  └─ the "looks" become the visual prompt ─┐
   ▼                                           ▼
Prompt Studio → Dataset → per-character LoRA → Expression + posture sprites
```

## Features

- **Character Studio** — the front door: start from one line ("a stoic elven blacksmith
  who secretly writes poetry") and let Ollama draft a full **character sheet** with you —
  identity, appearance, personality, backstory, behaviour quirks, speech style,
  relationships, scenario. It produces both a ready-to-import **SillyTavern V3 character
  card** (`chara_card_v3`; JSON + a PNG card using the character's own portrait) — with a
  first message, optional **alternate greetings** and **example dialogue**, and the option
  to **attach a lorebook** — *and* the **looks prompt** that seeds the visual pipeline, so
  persona and portrait share one identity. A later stage will *generate* a lorebook
  creatively (text-generation-webui / n8n). _(phase 8 — planned; see
  [PROJECT_PLAN.md](PROJECT_PLAN.md) §2 "Phase E")_
- **Campaign / Cast builder** — build a whole **cast** for a shared world (e.g. a *solo
  D&D game in SillyTavern*): multiple AI-driven characters + a **DM/narrator** sharing one
  generated **world lorebook**, cross-character relationships, a consistent art style, and
  **ST group-chat export**. The single-character builder above stays first-class — a
  campaign just runs it per cast member. _(later / post-1.0; see
  [PROJECT_PLAN.md](PROJECT_PLAN.md) §2 "Phase F")_
- **Build from a book** — drop in one or more **PDF / EPUB** books and let the tool read
  them to extract characters, world and relationships (a local RAG pipeline), then generate
  **cards + lorebook + campaign** grounded in the source — summarised into behavioural
  profiles, never copied verbatim. _(later / post-1.0; see
  [PROJECT_PLAN.md](PROJECT_PLAN.md) §2 "Phase G")_
- **Prompt Studio** — pick a checkpoint, refine a prompt against live previews (with
  click-to-zoom), then **sign off a baseline** that can never be lost.
- **AI prompt assistant** — describe a character in plain language and let Ollama author
  the character / style / negative fields, or **Modify** an existing prompt ("give her
  freckles and a longer coat"). Suggestions are editable with a one-click undo; prose,
  not tags, and expression words are kept out of the identity field.
- **Version history like a VCS** — every edit appends a new version with a diff of what
  changed; roll back to any point. Nothing is ever overwritten or deleted.
- **Persona library** — personas persist and reload. **Clone** one to vary it: the same
  character skiing *and* lazing on a beach. Clones record their parent so a trained LoRA
  can be reused instead of retrained.
- **Dataset builder** — generate a batch, pick the ones that look like the same person in a
  selectable grid, top up (+10) until you hit your target.
- **Per-character LoRA training** on your own GPU — native ComfyUI training with
  auto-captioning; validated end to end.
- **Pose / expression sets** — the 28 SillyTavern expressions with posture variation,
  tweakable one sprite at a time, then **exported as SillyTavern-named transparent PNGs**.
- **Service control** — Ollama has Connect / Unload (preload the model or free VRAM),
  and when ComfyUI or Ollama are down you can **Start / Restart** their containers from
  the sidebar. Container control goes through a scoped `docker-socket-proxy` (start /
  restart only, read-only socket) and is off unless `DOCKER_PROXY_URL` is set.
- **Logs tab** — filter by level and by `boot` / `integration` / `process` / `local`;
  also on stdout and in a rolling file so boot history survives a restart.
- Runs **entirely on your LAN**. ComfyUI stays external — this does not bundle it.

## Quick Start

**Prerequisites** — a reachable ComfyUI, and a folder both containers can share:

1. In Unraid, edit the ComfyUI container → **Add another Path**: your shared builds
   folder → container `/builds`, Read/Write.
2. Add `--output-directory /builds` to ComfyUI's `parameters.txt`, and restart it.

**Install** — only the `docker/` folder goes on the server:

1. Copy **`docker/`** to `/mnt/user/appdata/persona-forge/docker/`.
2. Edit `docker/.env` — set `COMFYUI_URL`, `BUILDS_HOST_PATH`, `DB_HOST_PATH` and
   `LOGS_HOST_PATH` (all paths **absolute**). It ships ready to use, so there is no
   file to rename.
3. Unraid **Docker Compose Manager** → point at
   `/mnt/user/appdata/persona-forge/docker/docker-compose.yml` → **Compose Up**.
4. Open `http://<server>:8890`.

The image is pulled from GHCR — no source and no build on the server. To update:

```bash
docker compose pull && docker compose up -d
```

On the server you end up with `db/` and `logs/` sitting alongside `docker/`:

```
/mnt/user/appdata/persona-forge/
├── docker/   compose + .env  (the only folder you copy)
├── db/       sqlite: personas, prompt history
└── logs/     rolling log file
```

**Check it worked:** both dots in the sidebar should be green — *ComfyUI* showing
latency and GPU, *Builds* showing `read/write`. If Builds is red, the bind mount or its
permissions are wrong and nothing downstream will work.

## Configuration

All settings live in `docker/.env`:

| Variable | Default | What it does |
|---|---|---|
| `COMFYUI_URL` | `http://192.168.1.33:9000` | Where ComfyUI lives |
| `BUILDS_HOST_PATH` | — | **Required.** Host path of the shared builds folder. Must be the same path mapped into ComfyUI as `/builds`. |
| `DB_HOST_PATH` | — | **Required, absolute.** Where the sqlite db + prompt history live, e.g. `/mnt/user/appdata/persona-forge/db` |
| `LOGS_HOST_PATH` | — | **Required, absolute.** Rolling log file, e.g. `/mnt/user/appdata/persona-forge/logs` |
| `PF_PORT` | `8890` | Published port |
| `PUID` / `PGID` | `99` / `100` | Ownership applied to build folders, so the ComfyUI container can write into them |
| `TZ` | `Europe/London` | Timezone |

Each persona gets a build folder in the shared root:

```
<builds-root>/<persona>/
├── lora/     trained LoRA for this character
└── images/   generated sprites
```

Finished sprites are **staged, never auto-copied** into SillyTavern — moving them into a
character's `expressions/` folder stays a deliberate manual step.

## Repo layout

| Path | What |
|---|---|
| `docker/` | The stack — **the only folder deployed to the server** |
| `backend/` | FastAPI app: orchestration, state, prompt versioning |
| `frontend/` | Web UI |
| `workflows/` | ComfyUI API-format templates + parameter manifests |
| `docs/` | Design notes and UI references |
| `PROJECT_PLAN.md` | Architecture, phases and open decisions |
| `CHANGELOG.md` | What changed in each release |

## Status

Prompt Studio, the Dataset builder, **per-character LoRA training** (validated) and the
**Pose / expression studio with SillyTavern sprite export** are all working — shipped
through **v0.6.2**. Next comes hardening (0.7), then **Character Studio (0.8)** — the
plain-language character-sheet front door described above, which adds the one piece the
pipeline never produced: the character *card* itself, not just the sprites. See the
roadmap in [PROJECT_PLAN.md](PROJECT_PLAN.md) and the release history in
[CHANGELOG.md](CHANGELOG.md).

Versioning is `0.<phase>.<iteration>` — the middle digit is the phase, the last
increments with each update inside it.

## Documentation

- [docs/ai-context.md](docs/ai-context.md) — cold-start orientation for a new AI session
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — goals, architecture, phases, open decisions
- [CHANGELOG.md](CHANGELOG.md) — per-release detail
- [docs/ui-style.md](docs/ui-style.md) — UI design direction

## Related projects

- [blender-mcp](https://github.com/rhamblen/blender-mcp) — the 3D/VRM avatar track
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — the generation engine

## License

MIT — see [LICENSE](LICENSE).
