# Setup & Installation

Everything you need to stand Persona Forge up: the **system requirements** (the wider
stack it orchestrates), the **quick-start install** on Unraid, and the **configuration**
reference. For what the app *is* and *does*, see the [README](../README.md).

---

## System Requirements

Persona Forge is the *studio* — it orchestrates other services and stages output for
SillyTavern. It does **not** bundle ComfyUI, an LLM, or SillyTavern; you point it at your
own. Here's the whole stack it expects, split into what runs **Persona Forge** and the
**SillyTavern side** its output feeds.

### Host

| | |
|---|---|
| **Platform** | **Unraid 6.12+** (built and deployed on it) — or any Linux host with **Docker + Docker Compose**. |
| **CPU / RAM** | Modest. The app is thin (FastAPI + SQLite) — ~2 vCPU / 2 GB is plenty; the heavy lifting is on the GPU services below. |
| **Disk** | A few GB for the image + `db/` + `logs/`; the shared **builds folder grows with datasets, LoRAs and sprite sets — budget ~1–5 GB per character**. |
| **Network** | One LAN. No internet needed at runtime (the image pulls from GHCR at deploy time). |

### Required services — Persona Forge won't function without these

Separate containers/apps that Persona Forge reaches over plain HTTP on your LAN.

- **NVIDIA GPU + drivers** — with the `nvidia-container-toolkit` so ComfyUI (and Ollama)
  can use it. Image generation is comfortable on **12 GB** (RTX 3060-class); **LoRA
  training wants 24 GB** (RTX 3090-class). See the VRAM-contention note below.
- **ComfyUI** — the generation engine (`COMFYUI_URL`). It needs:
  - at least one **checkpoint** (an anime/SDXL-family model for the default look);
  - the **native training nodes** (`TrainLoraNode`, dataset loaders, `SaveLoRA`) plus
    **Florence-2** for auto-captioning — used by the LoRA trainer;
  - the **BEN2** background-removal node — used by transparent-sprite export;
  - `--output-directory /builds` pointed at the shared folder, and `loras: /builds` added
    to `extra_model_paths.yaml` so trained LoRAs show up in the loader.
- **Ollama** — a local LLM for the plain-language prompt & character authoring
  (`OLLAMA_URL`, default `http://192.168.1.32:11434`), with at least one **instruct model**
  pulled (e.g. `llama3.1`; `mistral` / `phi3` also work).
- **A shared builds folder** — one host path bind-mounted as `/builds` into **both**
  ComfyUI and Persona Forge (Read/Write), owned `PUID:PGID` (`99:100`). This is the
  hand-off surface between them; if it isn't writable, nothing downstream works.

### Optional

- **docker-socket-proxy** (`tecnativa/docker-socket-proxy`) — powers the sidebar
  **Start / Restart** buttons for ComfyUI/Ollama. Scoped to start/restart on a **read-only**
  socket. Leave `DOCKER_PROXY_URL` unset and the feature (and its UI) disappears.

### The SillyTavern side — where the output goes

Persona Forge produces a **SillyTavern V3 character card** and a **named transparent-sprite
set** — it does not run SillyTavern. To actually *use* what it builds you'll want:

- **SillyTavern** — the role-play frontend. Import the generated card; drop the exported
  sprites into the character's `expressions/` folder (a deliberate manual step — Persona
  Forge stages them, never auto-copies).
- **An LLM backend — "the chatbot of choice"** — SillyTavern is only a frontend; it needs a
  text-completion backend to actually converse. Either:
  - **local** — text-generation-webui (Oobabooga), KoboldCpp, llama.cpp, or Ollama; or
  - **cloud API** — OpenAI, Anthropic, OpenRouter, etc.

  (This is *separate* from the Ollama that Persona Forge uses for authoring — they can share
  a box or run on different ones.)
- **TTS — optional, for spoken replies** — a SillyTavern TTS backend such as **chatterbox**
  (the `chatterbox-st` container on UR1), AllTalk, XTTS/Coqui, or the built-in browser
  voices. These are GPU-hungry and compete with ComfyUI for VRAM.
- **STT — optional, for voice input** — Whisper-based speech-to-text (SillyTavern's Speech
  Recognition extension or a local whisper server) if you want to *talk* to the character.
- **SillyTavern Extras — optional** — only if you use ST features (vector storage,
  summarization, image captioning) that need the separate Extras server.

> **VRAM contention (real, seen in development):** on a single shared card the 3090 is used
> by other GPU containers too (Ollama, `chatterbox-st`, Immich…) which can hold ~13 GB.
> ComfyUI's memory-free can't evict them, so a **LoRA training run can OOM under that aux
> load** even with nothing else actively generating. Stop the aux GPU containers for a
> training run, or give training its own card.

---

## Quick Start

Full stack is in [System Requirements](#system-requirements) above; the two setup
pre-steps are — **a reachable ComfyUI, and a folder both containers can share:**

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

---

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
