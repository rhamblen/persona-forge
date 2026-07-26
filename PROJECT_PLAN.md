# Persona Forge — Project Plan

> A self-hosted web app that turns the ad-hoc ComfyUI character workflow into a
> guided pipeline: **prompt → dataset → per-character LoRA → pose/expression
> sets**, with natural-language editing and full prompt rollback.
> Living document — edit freely.

Repo: https://github.com/rhamblen/persona-forge

Created 2026-07-23. Sibling to the VRM/Live2D avatar work in the parent repo's
`PROJECT_PLAN.md` (that track is 3D avatars; this track is 2D sprites + LoRAs).

---

## 1. Goal

**The problem.** Sometimes when using SillyTavern you want to create your own
characters with your own images — but you don't want a static image, you want
expressions. Live2D and VRM are complicated to build, and facial expressions alone
are easy to misread or simply not notice, especially across 28 of them. Adding
**posture** makes the character far more readable.

**The tool.** Persona Forge builds expression sets covering **both face and
posture** using ComfyUI, behind a custom interface that walks through the steps, with
**Ollama** providing natural-language prompt customisation where needed.

Concretely: type a character description, refine it visually, and come out the other
end with a **trained per-character LoRA** plus a **full expression/pose sprite set**
ready for SillyTavern — without hand-driving ComfyUI. Everything runs on the LAN
(ComfyUI + Ollama on UR1); no Claude Code in the runtime loop.

> **Why posture matters (design driver):** this is the reason the pipeline invests in
> a per-character LoRA at all. Face-only sets are trivially consistent but hard to
> read at a glance; varying posture needs the whole body re-generated, which only
> stays on-model with a LoRA (IPAdapter alone proved too drifty — see §9).

## 2. The pipeline (core UX)

Four phases, each gating the next. The user's spec, formalised:

### Phase A — Prompt Studio
- **Name the project first** → this creates the build folder
  `<builds-root>/<name>/` (with `lora/` + `images/` subfolders, see §5.1) and starts
  the session. Everything downstream writes under that folder.
- Enter a prompt (prose or tags); **pick the checkpoint/model**.
- Fire a single image; **refine the prompt** and re-fire until happy.
- **Natural-language edits** (via Ollama): "make her hair shorter, add glasses" →
  proposed prompt change → accept/reject.
- **Sign off** → the approved prompt + model + seed become a **locked baseline**
  that can never be lost (see Rollback, §4).

#### Persona library — save, reload, clone (added 2026-07-24)

A persona is not a one-shot session. The Prompt Studio must support:

- **Save + reload** — every persona's prompt is persisted and can be reopened later,
  not just started fresh. (Projects + their full version history already persist;
  the project selector is the reload path.)
- **Clone an existing persona** → a new project seeded with the original's current
  prompt, so it can be varied. The driving example: *the same woman, one dressed for
  skiing and one lazing on the beach.*

**Why this matters for LoRA cost:** identity lives in the **character** field, and
outfit/scene live in **style**. A clone that keeps the character but changes the
style is *the same person* — so it should be able to **reuse the parent's trained
LoRA rather than retraining**. Clones therefore record a `parent_project_id`, and
Phase C should offer "reuse parent LoRA" when one exists. That turns outfit/scene
variants from a ~1 hr training job into a prompt change.

### Phase B — Dataset Builder
- Generate a batch (default **30**) from the signed-off prompt, varied seeds/framing.
- Show them in a **selectable grid**; user picks the ones that look like the *same
  person*.
- If the selection is short of the target N, generate **+10 more** and repeat until
  N reached.
- Selected images become the **training dataset** for this character.

### Phase C — LoRA Trainer
- Auto-caption the dataset, configure, and **train a per-character LoRA** on the
  3090.
- Monitor progress; on completion the LoRA is registered and selectable.
- (One LoRA per character — this is the price of true consistency + pose freedom.)

### Phase D — Pose / Expression Studio
- Using the LoRA, generate the **expression + pose set** (the 28 SillyTavern
  expressions, and/or pose variants).
- Present them all in a grid; user selects any to **tweak — pose OR face**
  independently.
- Tweaks use the same NL editing + rollback. **Export** the finished set (correct
  SillyTavern filenames, transparent PNGs).

### Phase E — Character Studio (persona conception — the new front door) ⬜ NOT STARTED (added 2026-07-25)

> Logically this sits **upstream of Phase A**: you conceive the character in plain
> language first, and *both* the character's **looks** (→ Phase A prompt) and the
> SillyTavern **character card** (the persona text ST actually chats with) fall out
> of one coherent source. It is lettered E only because A–D already name the sprite
> pipeline; in run order it comes first.

**Problem it solves.** Persona Forge currently starts from a *visual* prompt and
ends at *sprites* — it never produces the **character card** itself. So today the
user writes the ST persona separately, and the looks and the persona can drift
apart. Character Studio makes the character's **identity the single source** that
drives both deliverables ST needs.

**Flow.**
1. **Seed** — user types a one-line concept ("a stoic elven blacksmith who
   secretly writes poetry").
2. **Field elicitation** — Ollama proposes a structured **character sheet** and
   drafts each field from the seed.
3. **Draft → refine** — per field: edit by hand, or ask Ollama to expand/rewrite
   *that one field*. Reuses the existing NL-edit machinery (Replace/Modify, the
   word-level diff, per-change accept/reject, Modify's verbatim-preservation hint)
   and the **append-only + rollback** store — now applied per character field.
4. **Coherence pass** — Ollama sanity-checks the sheet for internal consistency
   (appearance ↔ species, personality ↔ backstory).
5. **Two coordinated outputs** (below): the **looks prompt** → seeds Phase A; the
   **SillyTavern character card** → staged for import.

**The character sheet (fields — "what's required").**
- **Identity** — name, apparent age, gender/pronouns, species/race, role.
- **Appearance / looks** — build, height, hair, eyes, skin, distinguishing marks,
  typical outfit, **art style**. *This is the visual-generation field.*
- **Personality** — core traits, temperament, values.
- **Backstory** — origin, formative events, motivations.
- **Behaviour traits & quirks / mannerisms** — habits, tics, catchphrases.
- **Speech style / voice** — register (formal/terse/sarcastic) + sample lines.
- **Relationships** — significant people, allegiances, and stance toward `{{user}}`.
- **Likes / dislikes, goals, fears.**
- **Scenario** — the default setting the character is met in.
- **Greetings & dialogue** — `first_mes` (always authored), plus optional **alternate
  greetings** (`alternate_greetings[]`) and **example dialogue** (`mes_example`); Ollama
  drafts each on request.
- **Lorebook / world info** (optional) — attach or author `character_book` entries
  (see Output A).
- Optional: tone/content toggles, tags, creator notes.

**Output A — SillyTavern character card (V3).** SillyTavern accepts **Character Card
V3**, so target `chara_card_v3` (`spec: "chara_card_v3"`, `spec_version: "3.0"`) — a
superset of V2. Assemble the sheet into `data.{name, description, personality,
scenario, first_mes, mes_example, creator_notes, system_prompt,
post_history_instructions, alternate_greetings[], group_only_greetings[],
character_book, tags, creator, character_version, nickname, assets, extensions}`.
Stage into `<slug>/card/` as:
- **`.json`** (direct ST import), and
- a **PNG character card** — a portrait with the card embedded in the PNG (ST's native
  import format; V3 uses the `ccv3` tEXt chunk — a legacy `chara`/V2 chunk can be
  written alongside for older importers). The portrait is the character's **own**
  pipeline render (the signed-off Phase A base image), so card and sprites are one face.
- **Never auto-copied into ST** — same deliberate manual move as the sprites/VRM.
- Confirm V3 import + the exact tEXt-chunk mechanics against the **running** ST version
  at build time; the `.json` export is the safe fallback.

**Greetings & dialogue.** `first_mes` is always authored. Because V3 supports them, the
sheet also offers **alternate greetings** and **example dialogue** (`mes_example`) as
first-class *optional* fields — Ollama drafts each on request; the user adds/edits/
removes them freely. (Both exist in V2 too; V3 just makes them standard.)

**Lorebook (`character_book`) — two stages.**
- **Now (0.8):** the ability to **add / attach** a lorebook — import an existing
  lorebook / world-info JSON, or hand-author entries (keys → content + insertion
  settings), embedded as the card's `character_book` so it travels with the character.
- **Later:** **generate** the lorebook creatively from the character sheet, then let the
  user curate. Candidate backends (both already on the LAN): a **text-generation-webui**
  model — a more creative/long-form model than the instruct model used for prompt
  edits — and/or an **n8n** workflow orchestrating the model calls (fan out per topic:
  history, factions, locations, artefacts → structured entries). Its own milestone
  because it adds a new model backend + orchestration (§7).

**Output B — looks prompt → Phase A.** Distil the *appearance* field (+ art style)
into the **character prompt** and "Send to Prompt Studio," pre-filling Phase A's
character field so the same persona drives dataset → LoRA → poses → sprites. Honour
the standing rules: **prose, not tags**; **no expression words in the identity
prompt** (proven to leak a baked-in smile into `anger`/`grief` — see §9 / Key
decisions in `../CURRENT_STATE.md`).

**Persistence.** The sheet lives with the project — a new `character` table (field
versions) + a **`character.json` sidecar** in the build folder beside
`persona.json`. Reloadable and **cloneable** (reuse the persona-clone machinery:
vary the persona, keep or diverge the looks).

**Why it belongs here.** It closes the loop from *concept* to *both* things ST
needs — the chat persona **and** the matching sprite set — from one description,
entirely on the LAN (Ollama .32, no Claude in the runtime loop).

**Forward-compat for campaigns (Phase F, below).** Store each character so it can
later **belong to a campaign** and reference a **shared lorebook** + **other
characters** — don't hard-assume one isolated character per project. The existing
persona-clone / `parent_project_id` relatedness is the seed of that grouping.

### Phase F — Campaign / Cast builder (multiple AI-driven characters) ⬜ CONCEPT / post-1.0 (added 2026-07-25)

> **Driving use case (user):** run a **solo D&D game in SillyTavern** — one human
> player, a whole **cast of AI-driven characters** (party + NPCs) plus a
> **DM/narrator**, all sharing one world. Phase E makes *one* coherent character; a
> campaign needs *several* that are coherent **with each other** and with a shared
> world.

**Both modes ship — not one instead of the other.** Character Studio (Phase E) stays a
first-class **single-character** builder for one-off cards; the Campaign builder (Phase F)
**reuses it per cast member** and layers the shared world + DM + group export on top. A
campaign is "many Character-Studio characters + a shared world," so the single-character
path is never a dead end — it's the engine both modes run on.

**What a campaign adds on top of Character Studio.**
- **Campaign container** — a top-level grouping over multiple character projects:
  premise/setting, tone, **ruleset flavour** (e.g. D&D 5e), a **shared art style** (so
  the whole party's sprites match), and the **shared world lorebook**.
- **Shared world lorebook** — generate the *world* once (factions, locations, history,
  plot hooks) and **attach it to every cast member** (and the DM). This is the
  multi-character payoff of the stage-2 lorebook generator: one world, many cards. In
  ST it maps to a world-info/lorebook the **group chat** loads.
- **Cast** — each character is built with Phase E but **inherits** the campaign's art
  style + world lorebook, and its **relationships** field references *other cast
  members* (party dynamics, allegiances). Each still gets its own LoRA + sprite set.
- **DM / Narrator persona** — a special character that runs the game: knows the world +
  ruleset + how to narrate/adjudicate. May carry no sprites (a narrator) or a distinct
  "GM" avatar; seeded from a DM template + the campaign world.
- **Optional RPG stat block** — a per-character structured game-stats section (race,
  class, level, ability scores, notable abilities/inventory) embedded into the card
  description and/or a lorebook entry. Play is narrative, but stats aid consistency.
- **Coherence across the cast** — the Phase E coherence pass extends to cross-character
  checks: no duplicate names, mutually consistent relationships, everyone agrees on the
  shared world's facts.

**Export for group play.** Individual **V3 cards** for each cast member + the **shared
lorebook**, plus optional scaffolding of a SillyTavern **group chat** (party + DM).
Staged only — never auto-copied into ST.

**Depends on:** the stage-2 **lorebook generator** (for the shared world) — so it lands
*after* that, post-1.0. (Both likely lean on the same text-generation-webui / n8n
backend.)

### Phase G — Source ingestion: build from a book (PDF / EPUB) ⬜ CONCEPT / post-1.0 (added 2026-07-25)

> **User idea:** drop in **one or more books** (PDF / EPUB) and have the tool *read* them
> to build a **lorebook, character cards, and a campaign** grounded in that source
> material — instead of (or alongside) typing a concept by hand.

**This is an alternative *seed* for Phases E + F**, feeding the same charter-driven
generator; it adds a document-understanding front end. A book is far larger than any local
model's context, so this is a **retrieval (RAG) pipeline, not one prompt:**
1. **Intake & parse** — accept many PDFs/EPUBs per project. Extract clean text
   (`pypdf` / `pdfplumber` for PDF, `ebooklib` for EPUB; **OCR fallback** (Tesseract) for
   scanned PDFs). Preserve chapter structure; strip running headers/footers.
2. **Chunk → embed → index** — split into passages, embed with an **Ollama embedding
   model** (e.g. `nomic-embed-text`) into a lightweight **vector store** (sqlite-vec /
   Chroma). This is what lets a whole novel be queried on a small local model.
3. **Extraction passes (map-reduce over chunks)** — pull **characters, locations,
   factions, items, magic systems, events / history, relationships, timeline**.
4. **Consolidate (coreference)** — merge repeated mentions of one entity ("the knight" =
   "Sir Gawain") into a single **dossier per entity**, each carrying **source citations**
   (chapter / page) so generation stays grounded and checkable.
5. **Generate via the charter** — turn dossiers into **V3 cards** (PCs / NPCs / monsters /
   DM) + **lorebook entries** + a **campaign** setting / scenario, under the four
   priorities (consistency / behaviour / motivation / interaction), low-token for local
   models.
6. **Human curation** — review the extracted entity list, choose which become cards vs.
   lore, edit before generating; same versioning / rollback ethos as the rest of the tool.

**Transform, don't reproduce.** Cards and lore are **summarised, behavioural profiles with
citations — never verbatim book text.** This is the user's own private roleplay use, and
the "motivation over biography / consistency over length" priorities already pull toward
transformation rather than copying passages.

**Backends:** the PDF / EPUB / OCR parsers above + **Ollama embeddings** + a vector store,
on top of the **text-generation-webui / n8n** generation backend from the lorebook
generator. The heaviest piece in the plan — firmly post-1.0.

**Open questions:** parsing / OCR stack for messy scans; embedding model + vector store
choice; one-book-per-campaign vs. many-books-merged-into-one-world; how much
auto-generation vs. mandatory human review of extracted entities.

### Phase H — Emotional depth: progressive states + LoRA enrichment (added 2026-07-26)

> **Full design: [`docs/emotion-depth.md`](docs/emotion-depth.md).** Summarised here;
> that doc is the source of truth.

**The reframe.** Emotion is two layers, and collapsing them causes every design mistake:
the **state layer** (the character's internal condition — `anger 62, trust 85`, with
memory and progression) and the **sprite layer** (a *projection* of that state onto a
picture). SillyTavern has no state layer at all — its Expressions extension is a
**stateless label lookup**, so every message is classified independently and nothing
progresses. Phase H builds sprite resolution first, then state.

**H1 — Emotion-targeted enrichment (tactical, next phase).** The user's loop: *the basic
28 as startup, then focus the dataset on one emotion, enrich and grow, then come back to
the baseline for a different emotion.*

- **Axes × tiers.** Two dimensions — *which* emotion, and *how much*. The 28 ST labels
  are GoEmotions and **already encode tiers** (annoyance→anger, disappointment→sadness→
  grief), so grouping them by axis yields most of the ladder free; only ~6–8 top tiers
  (fury, terror, despair, elation…) are new, as **custom ST expression labels**. The 28
  stay the baseline and export target — tiers are additive.
- **Dataset layers.** "Come back to baseline" must be a **selection, not an undo**:
  `core` (immutable, from Phase B) + `emotion:<axis>` layers. A LoRA build declares which
  layers it trains on, and **always trains from scratch on their union** — never
  continued training, which compounds drift. `core` stays exactly reproducible forever.
- **Body language, not faces.** Rage and despair are posture, fists, tears, dishevelment
  — not a face repaint. Enrichment batches are **tier prompts × the full framing spread**,
  a new `mode="emotion"` alongside `faces`/`poses`/`both` in `_dataset_variation()`.
- **Caption the emotion explicitly** — the standing "expression words out of identity"
  rule is *why* this works: named emotions stay separable and promptable instead of being
  absorbed into the trigger word (which would make the character look angry at rest).
- **Guard rails** against self-amplification: core ≥50% of any training set, one emotion
  layer ≤~30%, mandatory human curation (the existing Phase B grid), and enrichment
  generated at reduced LoRA weight.
- **Selective rebuild.** Poses gain `axis`/`tier`/`lora_build_id`, so after a retrain only
  that axis's sprites re-render; the rest stay valid and **stale sprites are flagged**.
  Per-sprite revert, same rollback ethos as prompts.
- **Merges with the 0.7.x backlog item** "custom/editable dataset example prompts" — same
  machinery, plus an axis label. Build them together.

**H2 — The emotion state engine (post-1.0).** Emotion as *game state*: 4–5 axes scored
0–100, bucketed to tiers, with the **sprite derived** from the state rather than asked for.
The **LLM emits deltas** (`<mood anger="+20" trust="-15"/>`) and the **engine owns the
state** — applies, clamps, decays, persists — because LLMs judge scenes well and maintain
counters badly. State is richer than the sprite: `trust 85/anger 70` and `trust 20/anger 70`
render the same furious sprite but produce different dialogue. Needs a **temperament block**
(resting values, volatility, decay, ceilings) on the Phase E character sheet — which also
**decides which tiers get generated**, tying H2 back to H1. Runs in ST on built-ins
(variables + auto-executed Quick Replies + forced sprite + Author's Note injection); Persona
Forge **authors that bundle**, staged for manual import, and never runs it.

**Does this displace VRM?** Largely — see the design doc §5. A stock VRM rig has ~5 emotion
blendshapes against 28+ sprites here, so on emotional legibility the sprite track wins
outright and far cheaper. VRM keeps continuity, liveness, **lip sync** (real, given
chatterbox TTS), and 3D presence — but crossfades and an idle bob steal two of those.
Recommendation: **demote VRM to opt-in/experimental** and collapse the humanoid /
non-humanoid split — one sprite pipeline for the whole cast. Needs the user's confirmation
before the parent plan + avatar-strategy memory are rewritten.

### The AI design charter — the generator's brief (shared by E, F, G) (added 2026-07-25)

The Ollama-driven generator (and the later text-gen-webui / n8n lorebook engine) runs
under one **expert-designer system prompt** — the persona + principles all Character
Studio and Campaign output is authored against. User-specified 2026-07-25:

**Role.** An expert **AI Character Designer, World Builder, Dungeon-Master assistant, and
SillyTavern configuration specialist**, building complete, consistent, immersive
AI-roleplay environments compatible with SillyTavern.

**It authors:** character cards (V2/V3), NPCs, player characters, monsters, factions,
locations, items, magic systems, histories, campaign settings, lorebooks / world info,
relationships, memories, and scenario structures. These split cleanly across the data
model: **character cards** (PCs / NPCs / monsters / the DM) vs. **lorebook / world-info
entries** (factions, locations, items, magic systems, histories, campaign settings,
world-level relationships & memories) — which is exactly why *the world builder is the
lorebook generator* (Phase F).

**Output is optimised for local LLMs running through Ollama** — tight, structured, low
token count; this dovetails with "consistency over length" and keeps generations viable
on the small local models the tool runs.

**Design priorities (in order), enforced on every field and by the coherence pass:**
1. **Consistency over length** — short and coherent beats long and contradictory.
2. **Behaviour over description** — how they *act*, not how they read on paper.
3. **Motivation over biography** — *why* they do things, not a life-events dump.
4. **Interaction over static information** — written for play at the table, not a wiki.

These echo settled project rules (prose-not-tags; expression words kept out of identity;
the card is for *play*, not a static portrait), so they become both the generator's
**system prompt** and the **rubric** the coherence pass scores against.

## 3. Cross-cutting features

- **Ollama NL assistant** — one bounded job: given the current prompt + a plain-
  language instruction, return a revised prompt (+ a human-readable diff). Also
  handles prose↔tag translation. Runs as its own container; a small instruct model
  is enough.
- **Model + LoRA selection** — checkpoint picker in Phase A; LoRA picker in Phase D.
- **Export to SillyTavern** — write the set into a `<Character>/` folder with exact
  expression filenames. **Staged only — never auto-copied into the ST appdata.**
  Moving it into a character's ST `expressions/` folder is a deliberate **manual**
  step after the build is approved (same copy-in / permission gotcha as the VRM
  assets).
- **Logs tab** — a first-class view, not an afterthought. Filterable by **level**
  (`debug` / `info` / `warn` / `error`) and by **category**:
  - `boot` — startup: config, db init, mount checks. Survives restarts so a crash
    loop can actually be diagnosed.
  - `integration` — outbound calls to ComfyUI / Ollama: what was sent, status,
    latency, errors.
  - `process` — pipeline steps: project created, version saved/signed off/rolled
    back, generation queued → finished.
  - `local` — local processing: file/folder writes, image handling, db work.

  Records go to **stdout** (so `docker logs` works) **and** a ring buffer the UI
  reads, **and** a rolling JSONL file under `appdata/logs/` so boot history
  outlives a restart.
- **Settings** — a settings area holds:
  - **ComfyUI URL** (default `http://192.168.1.33:9000`) plus a **live connection
    status** indicator. The status is **pinned at the top of the left sidebar**
    (always visible, green/red), not buried in a settings page.
  - **Folder paths:** the **ComfyUI output** location, and the **builds root** where
    each project's `lora/` and `images/` subfolders are created. These paths must be
    on storage **shared with ComfyUI** (see §5.1). Explicitly **not** the ST folder.

## 4. Rollback / prompt versioning (hard requirement)

- Every prompt is an **append-only version** (id, parent, text, model, seed,
  source = manual|ollama, created_at).
- **Sign-off** pins a version as the baseline; it is immutable and always
  restorable.
- Any edit (manual or NL) creates a **child version**; the UI shows a timeline and
  lets you **roll back to any prior version**, especially the baseline.
- Nothing is ever destructive — "we never lose the agreed prompt we signed off on."

## 5. Architecture

```
┌────────────┐   HTTP    ┌─────────────────────┐   HTTP    ┌──────────────┐
│  Frontend  │◄─────────►│   Backend (FastAPI) │◄─────────►│  ComfyUI     │ UR1:9000
│  (React)   │  /api     │  orchestration +    │  /prompt  │  (existing)  │
│            │           │  state + versioning │  /history └──────────────┘
└────────────┘           │                     │   HTTP    ┌──────────────┐
                         │                     │◄─────────►│  Ollama      │ UR1
                         │                     │  /api/chat│  (new)       │
                         │                     │           └──────────────┘
                         │                     │   train_* ┌──────────────┐
                         │                     │◄─────────►│ ComfyUI-MCP  │ UR1:8878
                         └─────────┬───────────┘  or nodes │ (existing)   │
                                   │ SQLite + files        └──────────────┘
                                   ▼
                            appdata/ (persistent)
```

- **Backend — Python / FastAPI.** Async fits polling ComfyUI + calling Ollama.
  Owns: workflow submission (reusing our saved JSON templates), job polling, image
  fetch, prompt version store, dataset/LoRA/pose-set records, export.
- **Frontend — React (Vite + Tailwind)**, built into the backend's static dir so
  the whole thing ships as one image. Galleries with multi-select, prompt editor
  with version timeline, phase wizard.
- **ComfyUI — existing**, UR1:9000. We already have the workflow templates (base
  gen, IPAdapter poses, 28-expression, single re-roll). Backend parameterises and
  submits them.
- **Training — reuse the existing ComfyUI-MCP `train_*` flows** if they fit (they
  exist: `train_prepare_dataset`, `train_start`, `train_status`, …), else a small
  kohya/sd-scripts container. Decide during M3.
- **Ollama — new container.** Watch VRAM: it shares the GPUs with ComfyUI, so
  load/unload around generation or pin it to the 3060.
- **Storage — SQLite** (state, prompt history) + **filesystem** (images, datasets,
  LoRAs, exports). App state lives under `appdata/`; **build artefacts live under a
  shared builds root — see §5.1.**

### 5.1 Shared storage & build folders (critical constraint)

Persona Forge and ComfyUI run as **separate containers** on UR1, but they **must
share a filesystem view of the build data** — ComfyUI writes the generated images
there, and (crucially) has to be able to **load the trained LoRA** from there.

- **Builds root** — a single host share (its path is a Setting) mounted **read-write
  into BOTH** the persona-forge container and the ComfyUI container.
- **Per-build layout** — naming a project creates `<builds-root>/<name>/` with two
  subfolders:
  - `<name>/lora/` — the trained LoRA(s) for this character
  - `<name>/images/` — the finished 28 expression / pose sprites
- **ComfyUI must find the LoRA** — the builds root is added to ComfyUI's LoRA search
  path via `extra_model_paths.yaml` (it scans subfolders), so a freshly trained
  `<name>/lora/x.safetensors` becomes loadable immediately (a rescan/refresh may be
  needed right after training).
- **ComfyUI output** — either point ComfyUI's output at the build's `images/` folder,
  or have Persona Forge fetch results over HTTP `/view` and write them into the build
  folder itself. Decide at M0.
- **SillyTavern stays manual** — finished sprites remain in `<name>/images/`; they are
  **never** auto-copied to the ST appdata. The move into a character's ST
  `expressions/` folder is a deliberate manual step once the build is signed off.

**Settled paths (verified 2026-07-23):**

| | |
|---|---|
| Shared builds host path | `/mnt/user/data-and-backups/blender-and-comfyui-output/comfyui-builds` |
| Mapped into ComfyUI as | `/builds` (set in `parameters.txt`; confirmed via `/system_stats` → `argv`) |
| Mapped into persona-forge as | `/builds` (same host path, via `BUILDS_HOST_PATH` in `.env`) |

The path is deliberately **space-free** (it was originally `…/blender and comfy ui
output/comfyui builds`; spaces break compose's short `a:b` bind syntax and quoting in
`.env`). Compose still uses **long-form bind mounts** for robustness. Don't quote the
value in `.env`.

⚠️ That share tree **rejects SMB writes from Windows** (root-owned), so Claude cannot
stage files into it directly — the containers themselves write fine. Verification of
build contents is done by reading over SMB or via ComfyUI's `/view`.

**Settled 2026-07-23:** ComfyUI's output is relocated via its CLI-args file
`05-comfy-ui/parameters.txt` (`--output-directory <container path>`) — this image
uses that file, **not** env vars. The builds share is mounted into the ComfyUI
container with an Unraid **Add Path** mapping; the same host path is mounted into the
persona-forge container, with the path supplied via **`.env`**. (Docker-level flags
like `--runtime=nvidia` live in Unraid's *Extra Parameters*, a different field —
don't mix them.)

### 5.2 ComfyUI integration — direct HTTP API + workflow manifests

**Decision: the backend talks to ComfyUI over its native HTTP API. Not MCP.**
MCP is a wrapper for LLM tool-calling; routing an ordinary app through it just adds
a JSON-RPC hop to the same endpoints. (MCP remains useful for Claude-driven
development, not app runtime.)

Endpoints used (all verified in use 2026-07-22/23):

| Endpoint | Purpose |
|---|---|
| `POST /prompt` | Submit an API-format workflow; returns `prompt_id` |
| `GET /history/<prompt_id>` | Status + produced filenames |
| `GET /view?filename=&subfolder=&type=output` | Fetch a generated image |
| `GET /object_info` (or `/object_info/<Node>`) | Node schemas — powers **live dropdowns** for checkpoints, LoRAs, samplers, and validation |
| `GET /queue` | Queue depth / running state |
| `WS /ws?clientId=` | **Live progress events** (`progress`, `executing`, `executed`) — drives progress bars instead of polling |
| `GET/POST /userdata/workflows%2F<name>.json` | Read/write workflows in ComfyUI's own library |

**Selecting a workflow and changing values.** Workflows are stored as **API-format
JSON templates** in the repo; at runtime the backend loads a template, **patches
specific node inputs**, and POSTs it. To avoid hardcoding node IDs (brittle — IDs
shift when a workflow is edited), every template ships with a **parameter manifest**
mapping friendly names → node + input:

```jsonc
{
  "id": "expressions-28",
  "name": "28 Expression Sheet",
  "file": "workflows/28-expressions.json",
  "params": {
    "character":   { "node": "2",  "input": "value",       "type": "text" },
    "style":       { "node": "3",  "input": "value",       "type": "text" },
    "negative":    { "node": "4",  "input": "value",       "type": "text" },
    "checkpoint":  { "node": "1",  "input": "ckpt_name",   "type": "model", "model_type": "checkpoints" },
    "lora":        { "node": "40", "input": "lora_name",   "type": "model", "model_type": "loras" },
    "seed":        { "node": "11", "input": "seed",        "type": "int" },
    "denoise":     { "node": "20", "input": "denoise",     "type": "float", "min": 0.3, "max": 0.8 },
    "output_path": { "node": "22", "input": "output_path", "type": "path" }
  },
  "output_node": "22"
}
```

Benefits: the **UI auto-generates its controls from the manifest** (no per-workflow
frontend code), `/object_info` fills the model/LoRA dropdowns from live server state,
and adding a capability = dropping in a new template + manifest. The existing
workflows (28-expression, single re-roll, IPAdapter pose) become the first templates.

## 6. Repo & deploy structure

Follows the firm UR1 convention (memory `feedback-ur1-docker-deploy-convention`):
Claude edits local + instructs; **the user copies `appdata/` to UR1 and builds via
the Docker Compose Manager addon; Claude never builds/deploys, only verifies.**

```
persona-forge/                    # GitHub repo
├── README.md
├── PROJECT_PLAN.md               # this file
├── VERSION                       # 0.<phase>.<iteration>
├── docker/                       # <── ONLY this folder is copied to UR1
│   ├── docker-compose.yml        #     ComfyUI is external, not bundled
│   └── .env.example              #     COMFYUI_URL, BUILDS_HOST_PATH, ports
├── workflows/                    # API-format templates + parameter manifests
├── backend/                      # FastAPI app  (Claude edits)
│   ├── app/ …
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                     # React app    (Claude edits) → built into backend
│   └── …
├── appdata/                      # <── USER copies this to /mnt/user/appdata/persona-forge/
│   ├── config/                   #     settings, endpoints
│   ├── db/                       #     sqlite (state + prompt history)
│   ├── datasets/                 #     selected training images, per character
│   ├── loras/                    #     trained LoRAs
│   └── output/                   #     generated sprites / pose sets / exports
└── docs/
```

- **Image delivery:** prefer publishing to **GHCR via a GitHub Action** (like
  `blender-mcp`) so the user's build is a light `compose pull`; source-build on UR1
  is the fast-iteration fallback.
- **ComfyUI stays external** — the stack points at UR1:9000, it does not bundle it.

## 7. Build roadmap

**Versioning: `0.<phase>.<iteration>`** — the middle digit is the phase below; the
last digit bumps on each update/experiment within that phase. `1.0.0` = first
complete release. A `VERSION` file at the repo root tracks the current build.

- **0.1.x — Skeleton & deploy loop.** Repo scaffold, `docker-compose.yml`, `.env`,
  a hello FastAPI + minimal frontend showing the **left sidebar + ComfyUI connection
  status**, `appdata/` layout, shared builds mount. Proves the
  copy→compose→verify loop end to end.
- **0.2.x — Prompt Studio (Phase A).** Project naming → build folder, model picker,
  single-image generate against ComfyUI, prompt editor, **version store + sign-off +
  rollback** (VCS-style version view). No Ollama yet.
- **0.3.x — Ollama NL editing.** Stand up Ollama; wire "instruction → revised prompt
  + diff → accept/reject → new version." Prose↔tag helper.
- **0.4.x — Dataset Builder (Phase B).** Batch-30 gallery, multi-select, +10 top-up
  loop, persist the selected dataset. **Decide the training backend here.**
- **0.5.x — LoRA Trainer (Phase C).** Auto-caption, train, monitor, register the
  LoRA into the build's `lora/` folder. First run = recipe calibration.
- **0.6.x — Pose/Expression Studio (Phase D).** LoRA-driven expression/pose set,
  grid, per-sprite tweak (pose OR face) with NL + rollback, **SillyTavern staging**.
- **0.7.x — Hardening.** GHCR image + Action, `appdata/db` backups, run docs, polish.
  Dataset/LoRA-quality backlog (in priority order):
  1. **Raise the automated training-step default to ~1500–2500.** The last lever of the
     weak-LoRA recipe (0.7.2 pose variety + 0.7.3 close-ups/expressions fixed the dataset
     side; step count is what's left). `sweetie-pie` was trained at 500.
  2. **Custom / editable dataset example prompts (added 2026-07-25).** Beyond the automatic
     `DATASET_FRAMINGS × DATASET_EXPRESSIONS` rotation, let the user add their **own specific
     example shots** to a dataset batch — "make sure the LoRA sees *these*." Per-project,
     editable list (add / modify / remove); each entry is a framing/expression/style modifier
     appended through the same `expression` suffix (or a full prompt override), optionally with
     a per-entry count so a wanted example gets N candidates. Use cases: a specific outfit or
     accessory, a particular angle/pose, or an expression the auto-set doesn't nail. Also
     surface the built-in framing/expression lists as editable defaults so the whole variety
     set is user-tunable, not hardcoded. Queued alongside (or in place of) the auto-rotation;
     reconciles into `images(kind='dataset')` exactly like today, so training picks them up
     with no trainer change.
- **0.8.x — Emotional depth H1 (Phase H, added 2026-07-26).** ⚠️ *Proposed resequence —
  this takes 0.8.x and pushes Character Studio to 0.9.x, because H1 extends Phases B/C/D
  (which exist) and is needed sooner. User's call.* Emotion **axes × tiers** map (the 28
  regrouped + ~6–8 custom top tiers), editable per project; **dataset layers**
  (`core` + `emotion:<axis>`, core immutable); `mode="emotion"` enrichment batches (tier
  body-language prompts × full framing spread, emotion named in captions); a
  **`lora_builds`** table with layer-selected from-scratch training + rollback; and
  **per-axis selective sprite rebuild** with staleness flags and per-sprite revert.
  Absorbs the 0.7.x "custom/editable dataset example prompts" backlog item — same
  machinery with an axis label. Full design: `docs/emotion-depth.md`.
- **0.9.x — Character Studio (Phase E, added 2026-07-25).** Ollama-guided character
  sheet front end (§2 Phase E): seed → field elicitation → per-field NL refine +
  rollback → coherence pass. Two outputs: **looks prompt → Phase A**, and a staged
  **SillyTavern V3 character card** (`chara_card_v3`; JSON + PNG card using the
  pipeline's own portrait) — with `first_mes`, optional **alternate greetings** +
  **example dialogue**, and the option to **attach a lorebook** (`character_book`). New
  `character` table + `character.json` sidecar; clone-aware. Reuses the 0.3.x NL-edit +
  versioning machinery. (New scope — 1.0 now follows this.) **Also carries the Phase H
  temperament block** (resting values, volatility, decay, ceilings) on the character
  sheet, which decides which emotion tiers a given character needs generated at all.
- **1.0.0 — Release.**
- **Later (post-1.0) — Lorebook generator (Phase E, stage 2).** Creatively **generate**
  a character's lorebook from its sheet, then let the user curate — extending the
  attach-only support from 0.8. New backend: a **text-generation-webui** model (creative/
  long-form, distinct from the prompt-edit instruct model) and/or an **n8n** workflow
  orchestrating the calls. Its own milestone because it adds a model backend +
  orchestration; deliberately deferred past the 1.0 sprite-pipeline release.
- **Later (post-1.0) — Emotion state engine (Phase H2).** Emotion as game state: 4–5 axes,
  LLM-emitted deltas with the engine owning the score/decay/bucketing, and a generated
  **STScript + Quick Reply bundle** exported for manual ST import so sprites are driven by
  state rather than per-message classification. Depends on the H1 tier assets + the Phase E
  temperament block.
- **Later (post-1.0) — Campaign / Cast builder (Phase F).** Multiple AI-driven characters
  sharing one world (the solo-D&D-in-ST use case): campaign container, shared world
  lorebook attached to the whole cast, cross-character relationships + coherence, a
  DM/narrator persona, optional RPG stat blocks, and group-chat export. **Single-character
  Character Studio (0.8) stays a first-class standalone mode** — Campaign mode reuses it
  per cast member. Builds on the lorebook generator + the shared AI design charter (§2).
- **Later (post-1.0) — Source ingestion / "build from a book" (Phase G).** Ingest one or
  more **PDF/EPUB** books and extract characters/world/relationships via a **RAG pipeline**
  (parse + OCR → Ollama embeddings → vector store → map-reduce extraction → consolidated
  dossiers with citations), then generate **cards + lorebook + campaign** through the
  charter. Transform-not-reproduce (private use; summarised behavioural profiles). The
  most advanced piece; an alternative seed feeding Phases E + F.

### Future infrastructure — dedicated training GPU / parallel builds ⬜ HARDWARE-GATED (added 2026-07-26)

**Trigger: the user is swapping UR1's RTX 3060 (12 GB) for a second RTX 3090 (24 GB), giving
2× RTX 3090 24 GB.** That makes the following viable (it is *not* today — with only one
training-class GPU, training and generation contend for the same VRAM, and the 3060 is too small
and already shared with ollama+chatterbox):

- **Goal.** Run a LoRA build (train → first poses) on a **dedicated training GPU** while the main
  ComfyUI stays free for generation — so you can preview pictures, build a dataset, and queue the
  next character *while a build runs*. This is the real fix for the whole "background / concurrent
  builds" and "gen a picture while the build trains" thread (see §9 notes below).
- **Recommended approach (lowest risk): a second ComfyUI instance as the trainer.** Stand up a
  second `stable-diffusion-ComfyUI`-style container pinned to **GPU 2** via
  `NVIDIA_VISIBLE_DEVICES` (CDI form), **reusing the already-validated `lora-train.json` graph**
  (Florence-2 captioning, `SaveLoRA` → `/builds`) — no re-doing the training recipe. Persona Forge
  gains a **`TRAIN_COMFYUI_URL`** env: route **training + dataset staging** to the trainer
  instance, keep **generation** on the main instance. Models are shared through the same mount, so
  no duplicate downloads. Move ollama/chatterbox onto whichever card is *not* the trainer.
- **Alternatives.** (a) A dedicated **kohya / sd-scripts / ai-toolkit** trainer container — more
  capable (resume, sample previews, bucketing) but means re-validating captioning/dataset/format;
  worth it only if we outgrow ComfyUI training. (b) **RunPod cloud burst** — training on a rented
  GPU, local 3090s untouched; per-run cost, already an integration in the toolchain. Good as an
  overflow/ad-hoc path even after the second 3090 lands.
- **Rides the existing job engine.** The `lora_build` handler already orchestrates train→poses;
  pointing its ComfyUI calls at `TRAIN_COMFYUI_URL` is the core change. With a dedicated trainer,
  the serial-GPU constraint that made the engine one-job-at-a-time can relax into **lanes**
  (a GPU-2 training lane + a GPU-1 generation lane) — the `jobs` table already has room for a
  `lane` column (see `jobs.py`). This is also the enabler for the Phase F multi-character
  **add-to-queue** cast builder.
- **Related smaller lever (no hardware needed):** ComfyUI queue **front-insert** (`/prompt`
  `front: true`) lets a quick picture jump ahead of *pending* work (e.g. during the pose-render
  stage). It cannot preempt a *running* prompt (ComfyUI has no pause; `/interrupt` aborts), so it
  does not help during the single long training prompt — which is exactly why the dedicated
  training GPU is the real answer.

## 8. Open decisions (need your call)

- ~~**Name**~~ — decided: **Persona Forge**.
- **Frontend weight** — full React SPA (richer galleries) vs. a lighter HTMX/Svelte
  build. Recommendation: React, but open. **Style to follow the user's references
  (e.g. Shutter Hub) — see `docs/ui-style.md`.**
- **Training backend** — ComfyUI-MCP `train_*` flows vs. a dedicated kohya
  container (decided at M3 once we test the former).
- **Ollama model** — a small instruct model with good JSON/prose handling that
  coexists with ComfyUI on the GPUs (candidate: a Qwen/Llama instruct variant).
- **GHCR now or later** — publish images from the start, or source-build on UR1
  until the app stabilises.
- ~~**Character card spec version (Phase E)**~~ — **decided 2026-07-25: target V3**
  (`chara_card_v3`), which SillyTavern accepts; it's a superset of V2 and future-proofs
  the lorebook, alternate greetings, and example dialogue. Keep a **V2/`.json` export as
  the safe fallback**, and confirm V3 import + the exact PNG tEXt-chunk mechanics
  (`ccv3`, plus a legacy `chara` chunk for old importers) against the **running** ST
  version at build time.
- ~~**How much dialogue Ollama writes (Phase E)**~~ — **decided:** author `first_mes`
  always; offer **alternate greetings** + **example dialogue** as optional, editable
  fields Ollama drafts on request.
- **Campaign data model & ST group export (Phase F, post-1.0)** — how a campaign groups
  characters and shares one lorebook, and how far to scaffold ST **group chats** vs. just
  exporting the cards + shared world-info for the user to assemble. Also: does the
  DM/narrator get its own sprite set or stay text-only, and how much RPG mechanics
  (stat blocks) to formalise vs. leave narrative.
- **Source-ingestion stack (Phase G, post-1.0)** — PDF/EPUB + OCR parsing libraries, the
  embedding model + vector store, one-book-per-campaign vs. many-books-merged, and how
  much auto-generation vs. mandatory human review of extracted entities. Design rule
  fixed: **transform/summarise, never reproduce verbatim** source text into cards/lore.
- **Lorebook generator backend (Phase E stage 2, post-1.0)** — which local backend
  authors the lorebook creatively: a **text-generation-webui** model, an **n8n**
  orchestration workflow, or both. Needs a creative/long-form model (distinct from the
  prompt-edit instruct model) and a decision on where it runs (new service alongside
  Ollama vs. existing n8n). Attach-only lorebook support (0.8) does **not** depend on
  this.
- **Phase H sequencing + the VRM question (added 2026-07-26)** — (a) does H1 take **0.8.x**
  ahead of Character Studio (recommended — it's needed sooner and extends existing phases)?
  (b) Does emotional depth **displace the VRM track**? Recommendation: demote VRM to
  opt-in/experimental and run **one sprite pipeline for the whole cast**, since a stock VRM
  rig has ~5 emotion blendshapes vs. 28+ sprites; VRM's durable edge is **lip sync**, not
  expression. Confirming (b) means rewriting the parent `PROJECT_PLAN.md` avatar phases and
  the avatar-strategy memory.
- **Phase H design details** — cumulative vs. reset default when honing a second emotion
  (recommend cumulative, layers deselectable); hard-cap vs. warn on the ≤30% layer ratio
  (recommend warn + show the ratio); the exact **custom-expression-label mechanism on the
  running ST version**; which 4–5 axes H2 starts with (anger/fear/joy/sadness/trust); and
  whether temperament lives on the character sheet or its own emotion profile.
- **Builds root host path & LoRA wiring** (§5.1) — pick the exact UR1 share for the
  builds root, mount it into **both** containers, and decide how ComfyUI is pointed
  at it for LoRA loading (`extra_model_paths.yaml` pointing at the builds root, vs.
  mounting each build's `lora/` into ComfyUI's `models/loras`). Recommendation:
  `extra_model_paths.yaml` → builds root (handles new per-build folders
  automatically). Decide at M0/M3.

## 9. Risks & honest realities

- **LoRA consistency is the whole game.** IPAdapter alone gave "recognisable but
  drifty" across 4 tuning passes (hair/outfit/proportions wobble). The LoRA is what
  fixes this — but its quality depends on the **dataset cherry-pick** (a human
  step) and a **first-run calibration**. Budget ~1.5–2.5 hrs for the first
  character, faster after.
- **VRAM contention** — Ollama + ComfyUI (+ training) on the same GPUs. Plan to
  serialise heavy steps or pin Ollama to the 3060.
- **Region/asset gotchas carry over** — Civitai is 451-blocked from UR1; Manager
  3.x blocks URL model installs; `custom_nodes` is read-only over SMB
  (memory `project-comfyui-ur1-paths-and-env`). Model/LoRA installs go through the
  known-good routes.
- **Scope** — this is a multi-week build. M0–M2 deliver a genuinely useful Prompt
  Studio on their own, so value lands early even if later phases slip.

---
*Related memory: `project-persona-forge`,
`feedback-ur1-docker-deploy-convention`, `project-live2d-expression-workflow`,
`feedback-prose-prompts-over-tags`, `project-comfyui-ur1-paths-and-env`.*
