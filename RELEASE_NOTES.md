# v0.3.0 — AI prompt assistant, service control, preview zoom

**Phase 3 opens.** Persona Forge can now author prompts with a local LLM, manage the
Ollama model and the ComfyUI/Ollama containers from its own sidebar, and zoom previews.

## Added
- **AI prompt assistant (Ollama).** A card above the three manual fields. Describe a
  character in plain language and choose **Replace** (author all three fields fresh) or
  **Modify** (edit the current prompt) — one **Suggest** fills character / style /
  negative. Nothing is saved automatically: the suggestion lands in the editable fields
  with a **reject-and-undo** link, and only becomes a version when you Save.
  - Prose, not Danbooru tags; expression/emotion/pose words are kept out of the
    character field (a baked-in smile leaks into anger/grief). Verified live.
  - **Modify never destroys** a field the instruction didn't touch.
  - Native HTTP to Ollama (`/api/generate`, `format:json`). Endpoints `GET /api/ai/status`,
    `POST /api/ai/suggest-prompt`. Config `OLLAMA_URL` / `OLLAMA_MODEL`.
- **Ollama in the sidebar, with Connect / Unload.** Shows `offline` / `idle` / `loaded`.
  **Connect** preloads the model so the first suggestion is instant instead of a ~60s
  cold load; **Unload** frees VRAM. Suggestions carry `keep_alive` (`OLLAMA_KEEP_ALIVE`,
  default 30m) so the model auto-unloads when idle. `POST /api/ai/warm`, `/api/ai/unload`.
- **Start / restart ComfyUI and Ollama from the app.** Both are containers on the same
  host as Persona Forge. The sidebar offers **Start** (when stopped) and **Restart** for
  each; a ComfyUI restart is refused while its queue is busy unless forced.
  - Goes through a **scoped `tecnativa/docker-socket-proxy` sidecar**, never the raw
    socket — limited to list/inspect + start/restart, socket mounted **read-only**, on an
    **internal** network. Disabled unless `DOCKER_PROXY_URL` is set.
  - `GET /api/containers/status`, `POST /api/containers/{key}/start`,
    `POST /api/containers/{key}/restart?force=`.
- **Preview zoom** — click-to-zoom lightbox (backdrop or Esc collapses) + open-in-new-tab.

## Fixed
- **New personas defaulted to a photoreal checkpoint.** The default is now resolved
  (exact `DEFAULT_CHECKPOINT=animi/NoobAI-XL-v1.1`, else first match of
  `PREFERRED_CHECKPOINTS`, else position 0) instead of ComfyUI's folder order. Projects
  created earlier are corrected in the UI without a db migration.

**Image:** `ghcr.io/rhamblen/persona-forge:0.3.0`

## Upgrading
`docker-compose.yml` now defines a **second service** (`docker-socket-proxy`) and a
`networks:` block, and `docker/.env` gained the `OLLAMA_*`, `DEFAULT_CHECKPOINT`,
`PREFERRED_CHECKPOINTS` and `DOCKER_*` variables. Re-copy `docker/`, review `docker/.env`
(the Ollama URL and the two container names), then:

```bash
docker compose pull && docker compose up -d
```

Container control is optional — comment out `DOCKER_PROXY_URL` to run without the proxy.
