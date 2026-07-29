# Book ingest — one tool, two seeds (Phase G × Phase E)

> Design doc, written 2026-07-29. Folds the "LitRPG Interactive Simulation" proposal into
> the existing Phase G (source ingestion) and Phase E (Character Studio) specs.
> **Nothing here is built yet.**

## 0. The two corrections that shape this doc

**One tool.** Persona Forge is the product. A book-ingest front end is a *seed* for the
existing engine, not a second application. `lore-forge` (below) is a temporary
development harness for parallel testing, and it is designed to be deleted.

**Two seeds, one engine.**

```
  "a stoic elven blacksmith"  ──┐
                                ├──►  character sheet / dossier  ──►  everything downstream
  Dungeon Crawler Carl.epub  ───┘
```

A single character and a whole cast extracted from a novel converge on the **same
structure** — the Phase E character sheet. That convergence is the design constraint: if
the book path produces something the hand-typed path can't consume, the merge has failed.
This was already the plan's own framing (Phase G is "an alternative *seed* for Phases E +
F"); the user confirmed it explicitly.

---

## 1. The standing principle

> **Database = truth. LLM = storyteller.**

The project has now arrived at this three times independently:

| Where | Statement |
|---|---|
| `prompt-language.md` §8 | Don't ask an LLM to write a prompt string; ask for structure and compile the string yourself |
| `emotion-depth.md` (H2) | The LLM emits deltas; the **engine** owns the score, decay and bucketing |
| LitRPG proposal | The LLM must never be responsible for remembering you are level 14 |

Three arrivals from three directions makes it the architecture, not a preference. It is the
rule that governs every decision below: **the model extracts and narrates; it never
remembers and never arbitrates.**

---

## 2. The parallel harness — and its expiry conditions

Build the ingest pipeline as a standalone container (`lore-forge`, port 8891, own
`appdata/lore-forge/{docker,db,logs}`, same GHCR + copy-`docker/`-only deploy convention).

**Why temporarily separate.** The RAG stack (PDF/EPUB parsers, OCR, embedding client,
vector store) is a large dependency footprint with a different failure mode, and it is
long-running and CPU-bound where Persona Forge is GPU-contended. A broken parse must not be
able to take down the sprite pipeline mid-LoRA-build. Testing it alone also means a bad
extraction run is a deleted folder, not a corrupted persona database.

**Merge-first rules — non-negotiable, or the merge gets expensive:**

1. **Same conventions as PF.** FastAPI + SQLite, append-only versioning, `jobs` table +
   reconcile, seeded-table CRUD (the emotion-map / pose-library pattern), the same logging
   levels and categories.
2. **No schema that PF can't absorb.** Every table must be nameable as a PF table.
3. **The handoff is a file contract, never a shared database.** The harness writes a
   defined folder layout; PF reads it. This is what makes the merge an *importer* rather
   than a rewrite.
4. **No user-facing feature the merged tool won't keep.** The harness is not a place to
   experiment with UX.

**Merge trigger (user, 2026-07-29):** once the character-building process is complete —
i.e. Phase E Character Studio exists and the dossier → character sheet → looks prompt path
is proven. At that point the harness's endpoints move into PF as a Book tab, and the file
contract becomes an internal call.

---

## 3. What it produces

Seven artefacts in two groups. The split matters: group A is copied into SillyTavern by
hand; group B is read by the engine and never leaves the app.

### Group A — ST-ready (drop-in)

**A1. `worlds/<Book>.json` — the lorebook.** The primary deliverable and the first
genuinely useful output. **Verified against the live install:** ST lorebooks are
free-standing files in `default-user/worlds/`, shape `{"entries": {"<uid>": {…}}}`, with
this per-entry schema (read from the user's own `Eldoria.json`):

```
uid, key[], keysecondary[], comment, content, constant, selective, order, position,
disable, displayIndex, addMemo, group, groupOverride, groupWeight, sticky, cooldown,
delay, probability, depth, useProbability, role, vectorized, excludeRecursion,
preventRecursion, delayUntilRecursion, scanDepth, caseSensitive, matchWholeWords,
useGroupScoring, automationId
```

Entries are grouped by kind — locations, factions, magic/tech systems, history, artefacts,
terminology. **`key[]` alias harvesting is where extraction earns its keep:** "the Ashen
Court", "the Court" and "Ashenites" must land as three keys on one entry, or the lorebook
silently never fires. Set `order` / `depth` by kind so world *rules* sit deeper in context
than trivia.

**A2. `characters/<Name>.json` — V3 character cards.** JSON only, **not PNG**. The ingest
path has no ComfyUI and no portrait; Persona Forge owns the face. ST imports `.json`
directly, so this is immediately usable, and the PNG card (portrait + embedded `ccv3`
chunk) stays PF's job once a base image is signed off. That is a clean split rather than a
compromise — and it is exactly the merge seam.

**A3. `QuickReplies/<Book>.json`** — deferred until the runtime question (§5) is settled.

### Group B — engine inputs

**B1. `dossiers/<entity>.json` — the merge contract.** Per-entity structured extraction
with source citations: identity, appearance, personality, motivation, relationships, speech
samples, timeline of appearances. **This is the artefact the whole merge rests on** — it is
deliberately the shape of the Phase E character sheet, so Character Studio can prefill from
it instead of eliciting from a one-line seed. It also carries the appearance field that
becomes the Phase A looks prompt → dataset → LoRA → sprites.

Honour the standing rules on the way out: **prose, not tags**, and **no expression words in
the identity prompt** (proven to leak a baked-in smile into `anger` and `grief`).

**B2. `rules/system.json` — the LitRPG progression system.** XP formula, level thresholds,
skill-evolution conditions, class-evolution triggers, hard caps. This is the anti-"you
unlock God Slayer Level 9000" layer.

It is also **the highest-signal, lowest-ambiguity extraction target in the pipeline**,
because the genre states its own rules in-text, usually in literal system boxes. If any
extraction pass is going to work reliably on a small local model, it is this one — which
makes it a good early confidence test, not a late-stage luxury.

**B3. `story/graph.json` — story nodes.** Not a flat beat list. Nodes carry
`requirements[]` (`player_level >= 5`, `entered_dungeon`) and `outcome[]`, so the engine can
ask *is this reachable yet*, not merely *what comes next*.

**B4. `canon/constraints.json` — the three-tier canon table.**

| Tier | Meaning | Example |
|---|---|---|
| **must** | Has to happen; carries importance + allowed deviation | Jake discovers the dragon egg (critical) |
| **may** | Free to change | Sarah survives (medium) |
| **must-not-yet** | True, but not revealable before a point | The Emperor is the villain — not before Ch. 30 |

The third tier is the novel one and exists nowhere in the current plan. It is what
distinguishes "the AI knows the plot" from "the AI spoils the plot in message four."

**B5. `relationships/matrix.json`** — implied by the dossiers, but emitted separately
because the runtime reads it every turn and should not have to parse N dossiers to do it.

---

## 4. Where it goes on disk

A sibling of `comfyui-builds` on the same shared mount PF already uses:

```
/mnt/user/data-and-backups/blender-and-comfyui-output/lore-builds/<book-slug>/
  book.json            manifest — sources, hashes, models used, run config
  sources/             uploaded originals + extracted text, chapter-structured
  index/               chunks + embeddings (sqlite-vec)
  review/              extraction report, citations, coreference conflicts

  st-import/           ← MIRRORS ST'S OWN TREE, VERBATIM
    worlds/<Book>.json
    characters/<Name>.json
    QuickReplies/<Book>.json

  campaign/            ← engine inputs, NOT for copying into ST
    dossiers/  rules/  story/  canon/  relationships/
```

**`st-import/` mirroring ST's tree is the whole answer to "easy to integrate".**
Integration is *copy the contents of `st-import/` into `default-user/`* — no path
translation, no renaming, no per-file instructions, because the folder names are already
ST's own. Verified target:

```
\\192.168.1.33\appdata\STConfig\Data\default-user\
  characters\<Name>.png    the card
  characters\<Name>\       expression sprites  (Aisha currently has 6, not 28 —
                                                the archetype-tiering decision in the wild)
  worlds\<Name>.json       lorebooks
  groups\                  empty — no group chats yet
  QuickReplies\Default.json
```

`campaign/` is deliberately *outside* `st-import/` so runtime state never gets hand-copied
into ST by accident.

**Staged, never automatic** — the same rule as sprites and VRM, and it also sidesteps the
known appdata SMB write denial (reads work; writes from Windows are root-denied).

---

## 5. The runtime — and the one unknown that gates it

The proposal's architecture splits cleanly in two:

| | What | Shape |
|---|---|---|
| **Compiler** | Book → the seven artefacts. Run once per book. | Batch, offline, no GPU contention |
| **Runtime (Director)** | Holds live campaign state; sits on ST's message path | Live service, every message |

**Build the compiler first.** Its output is inspectable as files and provable without the
runtime existing. The runtime is a much larger commitment and depends on the following.

### What was verified on 2026-07-29

Read from the user's **actually installed** `Extension-Live2d/index.js`:

```js
import { eventSource, event_types, getCharacters } from '../../../../script.js';
import { extension_settings, getContext, ModuleWorkerWrapper } from '../../../extensions.js';
import { registerSlashCommand } from '../../../slash-commands.js';

eventSource.on(event_types.CHAT_CHANGED,     …);
eventSource.on(event_types.GROUP_UPDATED,    …);
eventSource.on(event_types.MESSAGE_RECEIVED, …);
```

So, confirmed present: an **event bus**, a **context accessor** (`getContext()`), and
**slash-command registration** (STScript). Third-party extensions do load on this install.

### What is NOT verified

Both installed extensions hook **`MESSAGE_RECEIVED` — after generation**. Nothing on this
box demonstrates a **pre-generation interception** hook, which is precisely what a Director
needs: read state → assemble a context packet → inject it → *then* let the model write.

This could not be settled because **the SillyTavern container is stopped** (`exited`, 2 days
ago) and ST's own `script.js` is inside the image, not in appdata. Note also the image is
`ghcr.io/sillytavern/sillytavern:staging` with an update pending — staging tracks the dev
branch, which is good for V3 card support but means the extension API can move underneath
us.

**This is the load-bearing unknown for the entire runtime half.** Settling it is cheap
(start ST, enumerate `event_types`, check for a generate-interceptor mechanism) and it
decides whether the Director is built as designed or needs a different mechanism entirely.
Do it before any runtime design work.

### Hardware gate

The proposal sizes the GM brain at 70B-class (Llama 3.3 70B / Qwen 2.5 72B). That is gated
on the **same second-3090 upgrade** already tracked in `PROJECT_PLAN.md` §7. Today there is
one 3090 plus a 3060, both contended with ComfyUI and training — demonstrated on 2026-07-29
when Immich plus a stray `minicpm-v` held 8.4 GB and killed a 1500-step build. **A 70B GM
and a LoRA build cannot coexist on one card.**

Extraction, by contrast, is explicitly fine on 8–14B, which the already-pulled
`gemma3:12b` covers — and which the Phase 9 bakeoff independently measured as the best
installed model for structured output.

---

## 6. Phasing

Each step is provable without the next.

| | Deliverable | How it's proven alone |
|---|---|---|
| **L0** | Intake + parse → clean chaptered text | Read the text; check the report |
| **L1** | Chunk + embed + index | Ask a question, get cited passages. **No generation involved** |
| **L2** | Extraction → dossiers + curation UI (promote to card / lore / discard) | Review the entity list |
| **L3** | **`worlds/<Book>.json`** | **First ST-usable output — aim here** |
| **L4** | V3 character cards (`.json`) | Import one into ST |
| **L5** | `rules/` + `story/` + `canon/` + `relationships/` | Inspect as files |
| **L6** | Merge into Persona Forge as a Book tab | Phase E exists by now |
| **L7** | Runtime / Director | Gated on §5 |

**L1 is the real risk checkpoint.** If retrieval is bad, everything above it is bad — and
you find that out before writing a single generation prompt.

**Prerequisite:** no embedding model is pulled on Ollama `.32` — `nomic-embed-text` or
similar is needed. `minicpm-v` is already there and is a plausible OCR fallback for
scanned pages.

---

## 7. Open questions

- **Intake source.** Upload-only, or read the existing **Calibre** library on UR1? Calibre
  gives clean EPUB plus metadata for free, and `kavita` is there too. Upload is simpler;
  Calibre is less work per book.
- **One book → one world file, or merged?** One-per-book is simpler. A series probably
  wants merging — the user already has a hand-made `Merged for Anya.json`, so this has
  been hit in practice.
- **How much auto-generation vs. mandatory human review** of extracted entities before
  cards are emitted.
- **Parsing / OCR stack** for messy scans; **vector store** choice (sqlite-vec vs. Chroma).
- **Where the Director runs** if §5 rules out an ST extension — an external service the
  card talks to, or something else.

## 8. Standing rules inherited

- **Transform, never reproduce.** Cards and lore are summarised behavioural profiles with
  citations — never verbatim book text. Private use; the charter's "motivation over
  biography, consistency over length" priorities already pull this way.
- **Staged, never auto-copied** into SillyTavern.
- **Prose, not tags**; no expression words in identity prompts.
- **Claude never builds or deploys containers**; only `docker/` is copied to the server.
