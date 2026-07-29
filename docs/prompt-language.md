# The Prompt Language — design doc (Phase 9)

> A precise, weighted, *structured* prompt format replacing the three free-text blobs, plus
> style presets (realistic / anime / comic) and a conversational editor that modifies the
> prompt instead of rewriting it. Written 2026-07-28 from the "can the AI prompt builder
> work in this terminology" discussion. Design doc — nothing here is built yet.

Three things the user asked for, which turn out to be one thing:

1. **Precision.** Work in real SDXL prompt terminology — weighted phrases like
   `(European interior scene:1.2)`, camera terms (`F/2.4`, `close-up`), a proper
   negative, and the render settings that go with them.
2. **Conversational modification.** "Now put her outside in the rain" edits the prompt in
   place; unrelated wording survives untouched.
3. **A style-first start.** Pick *realistic / anime / comic* up front and get a working
   positive + negative + sampler settings, not a blank box.

They are one thing because (2) only works properly if (1) is stored **structurally**. As
long as a prompt is a text blob, "modify" means "ask an 8B model to retype 400 words
verbatim and change six of them" — which is exactly the failure 0.3.4 had to fight with
prompt engineering. Store the prompt as *segments* and modification becomes a small,
validated **edit script** that structurally cannot drop text it didn't mention.

---

## 0. The reference prompt

The whole design is calibrated against a real prompt of the user's:

```
1girl, Underwear, bikini, lace, Pretty Europe girl, (European interior scene:1.2),
Dark room, The art of contrast photography, Gently beautiful posture, large breast,
Beautiful hips, Well-proportioned figure, Fine facial details, A cinematic shot,
(Low light shooting:1.3), octane render, Soft ambient light, Exquisite facial features,
F/2.4, close-up, beautiful studio soft light, vibrant details, hyperrealistic, elegant,
beautiful background, 8k, best quality

Negative: bad anatomy, bad hands, multiple eyebrow, (cropped), extra limb, missing limbs,
deformed hands, Long neck, two heads, bad breasts, bad butt, long body, (bad hands),
signature, username, artist name, conjoined fingers, deformed fingers, ugly eyes,
imperfect eyes, skewed eyes, unnatural face, unnatural body, error,
painting by bad-artist, (worst quality:1.5), (low quality:1.5), (normal quality:1.5), lowres

Steps: 40 · CFG: 8 · Sampler: dpmpp_2m_sde_gpu · Model: copaxRealisticXLSDXL1_v32 · 960×1280
```

Read it carefully and it is **not** a Danbooru tag list. `The art of contrast photography`,
`Gently beautiful posture`, `beautiful studio soft light` are short prose phrases. What
makes it precise is that they're **segmented by comma and optionally weighted** — the
comma is a separator between independent ideas, and `(x:1.2)` is emphasis.

That matters, because it resolves the standing rule.

### The "prose, not tags" rule, restated

`docs/ai-context.md` says *prompts are prose, not Danbooru tags*, because tag rewrites drop
detail and break garments — `flowing silk evening gown with lace trim` must never become
`dress`. That rule stays, but its wording is wrong for what we're building. It becomes:

> **Prompts are weighted phrase segments.** Each segment is a short prose phrase, never a
> single booru token (`blue_eyes`, `1girl` excepted as the subject anchor). The model may
> add, remove or reweight segments; it may **never** rewrite the user's phrasing inside a
> segment it wasn't asked to change.

Same spirit, enforceable structurally instead of by asking a model nicely.

---

## 1. The data model — segments

A prompt is an ordered list of typed segments.

```json
{
  "id":      "sty.set.1",
  "slot":    "setting",
  "text":    "European interior scene",
  "weight":  1.2,
  "enabled": true,
  "locked":  false,
  "origin":  "preset:photoreal"
}
```

| Field | Why it exists |
|---|---|
| `id` | Stable handle. The edit script targets ids, so a rename never orphans an op. |
| `slot` | Semantic group (below). Drives ordering, grouping in the UI, and preset merges. |
| `text` | Short prose phrase. Verbatim as the user wrote it. |
| `weight` | `1.0` emits bare; anything else emits `(text:1.2)`. Clamped **0.5–1.6**. |
| `enabled` | Toggle a term off **without deleting it** — A/B the same seed to see if `octane render` is actually earning its place. This is the single most useful prompt-debugging affordance and it's free once segments exist. |
| `locked` | The AI may not touch it. Enforced **server-side** — ops targeting a locked segment are rejected and reported, not merely discouraged in the system prompt. |
| `origin` | `preset:<id>` / `user` / `ai`. Colours the chip, and lets "change style preset" swap out preset segments without disturbing anything the user wrote. |

### Slots

Slots map onto the **existing three fields**, so the identity/expression separation that
the whole project depends on is preserved by construction.

**`character`** — fixed identity only. *Never* expression, mood, pose or framing.

| Slot | Example from the reference prompt |
|---|---|
| `subject` | `1girl`, `solo` |
| `age_build` | `young adult woman`, `well-proportioned figure` |
| `face` | `fine facial details`, `exquisite facial features` |
| `hair` | — |
| `eyes` | — |
| `skin` | — |
| `body` | `large breast`, `beautiful hips` |
| `outfit` | `underwear`, `bikini`, `lace` |
| `accessories` | — |
| `marks` | freckles, scars, tattoos |
| `misc` | anything unclassified (always available — see §6) |

**`style`** — look, scene, light, camera. Changes per shot.

| Slot | Example |
|---|---|
| `medium` | `photograph` / `anime illustration` / `comic panel` |
| `art_direction` | `the art of contrast photography`, `octane render` |
| `setting` | `(European interior scene:1.2)` |
| `time_light` | `dark room`, `(low light shooting:1.3)`, `soft ambient light` |
| `camera` | `a cinematic shot`, `F/2.4`, `close-up`, `beautiful studio soft light` |
| `composition` | rule of thirds, centred, wide shot |
| `palette` | `vibrant details` |
| `quality` | `hyperrealistic`, `elegant`, `8k`, `best quality` |
| `misc` | |

**`negative`**

| Slot | Example |
|---|---|
| `anatomy` | `bad anatomy`, `bad hands`, `extra limb`, `conjoined fingers`, `long neck`, `two heads`, `long body` |
| `face_defects` | `ugly eyes`, `imperfect eyes`, `skewed eyes`, `multiple eyebrow`, `unnatural face` |
| `artifacts` | `signature`, `username`, `artist name`, `cropped`, `error`, `painting by bad-artist` |
| `quality_floor` | `(worst quality:1.5)`, `(low quality:1.5)`, `lowres` |
| `style_bleed` | *the preset's* opposite — `anime, cel shading` in a photoreal preset; `photo, 3d, realistic skin texture` in an anime one |
| `content` | the user's own exclusions |

**Pose/expression stays exactly where it is** — the `expression` workflow param and
`poses.modifier`. It gets the same segment treatment (`pose`, `expression`, `gaze`,
`gesture`) so the pose editor and `ollama.revise()` inherit the machinery, but it is
*never* merged into `character`.

### Where it's stored

Add **one column**: `prompt_versions.segments_json`. Keep `character` / `style` /
`negative` as the **compiled** strings.

This is the same trick as `lora_stack_json` (0.8.0), and it's chosen for one reason:
**nothing downstream changes.** ComfyUI, `persona.json`, the dataset builder, poses and
export all keep reading the three text fields exactly as they do today. Legacy versions
have `segments_json = ''` and still render — compile is the identity function on a blob.
Backfill is opportunistic: the first time a legacy version is edited, parse it into
segments (§3) and store both.

Append-only versioning is untouched, and segments roll back with the prompt for free.

---

### 1b. The full A1111 syntax (per the guide)

Reference: [stable-diffusion-art.com/prompt-guide](https://stable-diffusion-art.com/prompt-guide/).
The segment model has to survive **all** of it, not just `(x:1.2)` — verified in
`test_syntax.py`:

| Syntax | Meaning | Handling |
|---|---|---|
| `(kw:1.2)` | explicit weight | → `weight` |
| `(kw)` / `((kw))` / `(((kw)))` | ×1.1 / ×1.21 / ×1.33 | multiplicative unwrap → `weight` |
| `[kw]` / `[[kw]]` / `[[[kw]]]` | ×0.9 / ×0.81 / ×0.73 | multiplicative unwrap → `weight` |
| `[from:to:factor]` **as a whole phrase** | prompt scheduling / keyword blending | structured `schedule` field, editable |
| `[from:to:factor]` **inline in a phrase** | same, mid-sentence (the guide's own examples) | kept **opaque inside the segment text**; detected and badged, round-trips verbatim |
| `BREAK` | start a new 75-token CLIP chunk | its own segment; draggable |

Two traps found by testing rather than by reading:

- **`[x]` and `[a:b:f]` share the bracket.** De-emphasis and scheduling are only
  distinguishable by the colon-separated factor. Try the schedule pattern first.
- **Schedules are usually inline, not phrase-level** (`holding an [apple: fire: 0.9]`).
  Trying to structure those would mangle them. Detect, badge, leave as text — the
  round-trip is what matters, structured editing is a bonus.

### 1c. The token budget

CLIP encodes in **75-token chunks**, independently, and a token at the *start* of a
chunk carries more weight. Two consequences the segment model should surface:

- **Canonical slot order is load-bearing**, not cosmetic — it's why `subject` leads and
  `quality` trails.
- **Show a token counter and mark the chunk boundary.** The reference prompt is ~116
  tokens — it spills into a second chunk, and `Soft ambient light` straddles the
  boundary, getting split across two independent encodings. The user cannot currently
  see that. `BREAK` is the fix, and it should be one click.

The guide's own warning applies to the reference prompt: *more is not always better*.
56 segments is a lot, and a prompt-health hint ("~116 tokens, 2 chunks, 3 near-duplicate
quality terms") is cheap once segments exist.

### 1d. Cheap wins that fall out of segments

- **Solo a segment.** The guide's "checking keywords" advice — render a keyword alone to
  see whether the model even knows it. One click on a chip.
- **Duplicate detection.** The reference prompt contains `bad hands` **twice** — once
  plain, once as `(bad hands)` = ×1.1. A text blob hides that; a segment list shows it
  immediately. (Found by the importer on the first run.)

---

## 2. The compiler (segments → the string ComfyUI gets)

Deterministic, no model involved:

1. Drop `enabled: false`.
2. Sort by canonical slot order (the table order above). SDXL weights earlier tokens more,
   so subject leads and `quality` trails — the ordering is part of the design, not cosmetic.
3. `weight == 1.0` → `text`; otherwise `(text:{w})` at 2 dp, trailing zero stripped.
4. Join with `, `.
5. **Escape literal parentheses** in `text` as `\(` `\)` before step 3. Non-negotiable —
   an unescaped paren in a user phrase silently corrupts every following weight.

ComfyUI's `CLIPTextEncode` parses A1111 `(x:1.2)` syntax natively, so the compiled string
drops straight into the existing workflows with no graph change.

---

## 3. The parser (a string → segments)

The on-ramp, and it matters more than it looks: the user already has a library of prompts
in the reference format. **Paste one in and get a working project.**

`POST /api/prompt/parse` takes a full A1111-style block — positive, `Negative prompt:`,
and the `Steps: / CFG scale: / Sampler: / Model: / width: / height:` trailer — and returns
segments plus render settings plus a checkpoint match.

Classification, cheapest-first:

1. **Split** on top-level commas (respecting escaped parens), strip `(…:w)` into `weight`.
2. **Lexicon** — a static phrase→slot table (a few hundred entries, shipped in the repo)
   resolves the overwhelming majority offline and deterministically. `F/2.4` → `camera`,
   `bad hands` → `anatomy`, `8k` → `quality`.
3. **Heuristics** — an `f/` or `mm` token is `camera`; a bare `(x:1.5)` in the negative
   next to `quality` words is `quality_floor`.
4. **Ollama fallback**, one batched call for the leftovers: "assign each of these phrases
   to one of these slots." Low-risk — a misfile is a chip in the wrong group, fixed by drag.
5. Anything still unresolved lands in `misc`. Never blocks the import.

---

## 4. Style presets — the "start here" step

A preset is a **seed bundle**, applied at project creation and re-appliable later.

```yaml
id: photoreal
label: Photographic / realistic
checkpoint_hint: copaxRealisticXLSDXL1     # fuzzy-matched against installed checkpoints
render: { steps: 40, cfg: 8.0, sampler: dpmpp_2m_sde_gpu, scheduler: karras,
          width: 960, height: 1280 }
positive:
  medium:        [ photograph ]
  art_direction: [ the art of contrast photography, octane render, a cinematic shot ]
  camera:        [ F/2.4, beautiful studio soft light ]
  quality:       [ hyperrealistic, vibrant details, 8k, best quality ]
negative:
  quality_floor: [ {text: worst quality, weight: 1.3}, {text: low quality, weight: 1.3},
                   lowres ]
  anatomy:       [ bad anatomy, bad hands, deformed hands, conjoined fingers,
                   deformed fingers, extra limb, missing limbs, long neck, two heads,
                   long body ]
  face_defects:  [ ugly eyes, imperfect eyes, skewed eyes, multiple eyebrow, unnatural face ]
  artifacts:     [ signature, username, artist name, cropped, error ]
  style_bleed:   [ anime, cel shading, illustration, 3d render ]
```

Four shipped presets:

| Preset | Character |
|---|---|
| `photoreal` | The reference prompt, generalised. Photographic vocabulary, lens/lighting terms, `anime/cel shading/3d` in `style_bleed`. |
| `anime` | `anime screencap, cel shading, clean lineart, flat vibrant colours`. **No** `F/2.4` or `octane render` — photographic vocabulary actively confuses an anime checkpoint. `style_bleed`: `photo, photorealistic, 3d, realistic skin texture`. Lower CFG (≈5–6), fewer steps (≈28). |
| `comic` | Inked linework, halftone, bold flats, panel framing. `style_bleed`: `photo, 3d, soft airbrush`. |
| `clean` | Nothing but a quality floor — for users who want to build from bare metal. |

Three notes on making presets actually work:

- **`style_bleed` is what makes the choice stick.** The positive words nudge; the negative
  words are what stops an anime checkpoint drifting photoreal. Don't ship a preset without
  its opposite in the negative.
- **Checkpoint beats vocabulary.** A preset nominates a checkpoint via the existing default
  resolver. If the user picks `anime` with a photoreal checkpoint loaded, show a **soft
  banner** ("this preset expects an anime checkpoint") — never block it. Deliberate
  mismatch is a legitimate technique.
- **`(worst quality:1.5)` is on the strong side.** The reference prompt's 1.5 trio can
  flatten contrast and desaturate. Ship the default preset at **1.2–1.3** and let the user
  push it up; it's one weight stepper.

**Presets are editable and user-addable**, seeded from defaults on first boot into a
`style_presets` table with CRUD + reset — the **exact pattern the emotion map already uses**
(0.8.2). Reuse it rather than inventing a second config mechanism. "Comic" in particular
spans western ink, manga and cel; the answer to that is *clone and edit*, not three more
built-ins.

### Render settings need a home

`prompt_versions` has no `steps` / `cfg` / `sampler` / `width` / `height` — today they're
workflow defaults. The reference prompt carries them, and the user rightly treats them as
part of the prompt. Add **`prompt_versions.render_json`** (same one-column trick, rolls back
with the prompt for free). The `base-character` manifest already exposes every one of these
params, so wiring is just passing them through in `_queue_*`.

---

## 5. Conversational modification — the edit script

**Today:** instruction + three text blobs → three text blobs back, and a word-level diff to
catch what the model silently ate.

**Proposed:** instruction + the segment table → a list of **ops**.

The model sees a compact numbered view, not JSON:

```
STYLE
 [sty.med.1] medium: photograph
 [sty.set.1] setting: European interior scene (1.2)
 [sty.lgt.1] time_light: dark room (1.3)          🔒 locked
 [sty.cam.1] camera: close-up, F/2.4
```

and returns:

```json
{"ops": [
  {"op": "replace",    "id": "sty.set.1", "text": "rainy Paris street at night"},
  {"op": "add",        "slot": "time_light", "text": "neon reflections on wet pavement",
                       "weight": 1.1},
  {"op": "set_weight", "id": "sty.cam.1", "weight": 1.2},
  {"op": "disable",    "id": "neg.style_bleed.1"},
  {"op": "render",     "key": "steps", "value": 50},
  {"op": "note",       "ids": ["sty.lgt.1"], "text": "dark room at 1.3 fights the new outdoor setting"}
]}
```

Why this is strictly better than returning text:

- **Unmentioned text cannot be lost.** Not "the model was told not to" — it structurally
  has no way to. The entire 0.3.4 verbatim-reproduction problem disappears.
- **A small model can do it.** It emits ids and short strings, not 400 tokens of verbatim
  prose. Faster, and far more reliable on llama3.1:8b.
- **One op = one accept/reject chip.** The 0.3.3 per-change UI gets *simpler* and exactly
  accurate, instead of being reverse-engineered from a word diff.
- **Locks are enforceable.** The server rejects ops on locked segments and reports
  "4 changes applied, 1 skipped (time_light is locked)".
- **Auditable.** The version note writes itself: *"AI: setting → rainy Paris street at
  night; +neon reflections; camera 1.0→1.2; steps 40→50"*.
- **`note` is free value.** A read-only op that flags conflicts — `dark room` at 1.3 against
  `beautiful studio soft light`, or `close-up` against `well-proportioned figure` (you
  can't frame both). "What's fighting me here?" becomes a first-class question.

### Validator (server-side, always)

Unknown `id` → drop the op. Weight out of range → clamp. `add` to an unknown slot → route to
that field's `misc`. **Cap ops per turn** (≈12) so a confused model can't nuke a prompt.
Malformed JSON → one repair retry with the parse error attached; on a second failure, fall
back to the current whole-text mode and say so.

### The chat rail — and why it *is* the version history

The user's framing is "as I use the prompt, I ask it to modify things." So: a **chat rail
beside the prompt**, per project. Each accepted turn writes a new `prompt_versions` row with
`source='ollama'` and the generated note.

That means **the conversation and the version rail are the same object**. The VCS-style rail
already built in Phase 2 becomes the chat transcript, and rollback becomes "go back three
things I said." No new history mechanism.

Context handling: send the last ~6 turns so "make it warmer" after "put her outside"
resolves, but **always send the current segment table as authoritative** — never let the
model work from its own memory of the prompt state.

---

## 6. UI

Prompt Studio becomes two panes plus a header.

- **Header** — style preset selector · checkpoint · render settings row (steps/CFG/sampler/size).
- **Left: the segment editor.** Chips grouped by slot, per field. Each chip carries text, a
  weight stepper, 🔒, 👁 (enabled), ✕, and an origin dot. `+` per slot. Drag to re-slot.
- **Below it: the compiled prompt** in a monospace box, read-only by default — with an
  **"edit as text"** escape hatch that reparses on blur.
- **Right: the chat rail.** Instruction box, turn history, each AI turn showing its ops as
  accept/reject chips, then **Apply → new version**.

The escape hatch is not optional. Power users paste. If the structured editor is the only
way in, the tool is worse than a textarea; if it's a *view over* text you can always drop
into, it's strictly better.

---

## 7. Rollout

Phase 9, versioned `0.9.x`. Each step ships something usable on its own.

| Version | Deliverable |
|---|---|
| **0.9.0** | **The grammar.** `segments_json`, compiler, parser, paste-an-A1111-block importer. Chip UI with weights, lock, enable. AI unchanged. *Weights and structure are worth having before any AI work lands.* |
| **0.9.1** | **Presets.** `style_presets` table (emotion-map pattern: seeded, CRUD, reset), `render_json` on versions, checkpoint hint + mismatch banner. photoreal / anime / comic / clean. |
| **0.9.2** | **The edit script.** Ollama returns ops; validator; per-op accept/reject replaces the word diff; server-side lock enforcement. |
| **0.9.3** | **The chat rail.** Multi-turn, history = version history, auto-written notes, `note` conflict flagging. |
| **0.9.4** | **Polish.** Lexicon expansion, preset import/export as YAML, per-slot bulk reweight ("emphasise outfit"). |

## 8. Where novel-ingest fits (and why it's the same object)

A two-stage design has been proposed separately for the LitRPG ingest path:

```
Book → extract scene → LLM analyses (who, emotion, pose, clothing, lighting,
       camera, location, props, style) → structured JSON → prompt builder → prompt
```

That is **the same architecture as this document**, arrived at independently, and the
agreement is worth stating plainly: *don't ask an LLM to write a prompt string; ask it
for structure and compile the string yourself.* The extraction schema above maps almost
one-to-one onto the slots in §1 — `clothing`→`outfit`, `lighting`→`time_light`,
`location`→`setting`, `camera`→`camera`, `props`→`details`.

The one thing to add: in that sketch the JSON is a **transient intermediate** — it exists
for one hop and is thrown away. Here it is **persistent state**. That difference is what
makes conversational editing possible at all: "now make it rain" needs something to
mutate, and a discarded intermediate gives it nothing to hold onto.

So they compose rather than compete:

| Stage | Produces | Model job |
|---|---|---|
| **Ingest** (novel → persona) | segments, `origin: "extract"` | long-context comprehension, extraction |
| **Edit** (this doc) | ops against those segments | schema adherence, minimal edits |
| **Compile** | the prompt string | *no model at all* |

One consequence for model choice: **these are different jobs and need not be the same
model.** Extraction wants long context and reading comprehension; the edit script wants
tight instruction-following and schema discipline, and is measurably indifferent to
model size (§9). The app already selects a model per call, so this costs nothing.

---

## 9. Model selection — measured, not assumed

Full results in **`prompt-language-MEASURED.md`**. Headline: **`gemma3:12b`** — best
correctness (6/7 edits right), best worst-case, and fastest at 4.8s/edit. Keep
`llama3.1` as fallback; don't use `deepseek-r1` for this (reasoning tokens buy nothing
here and cost latency).

Three findings that change this document:

- **Risk #1 is retired.** Ollama 0.32.4 supports JSON-schema-constrained decoding:
  **7/7 valid JSON on every model**, including a 3.8B one. Keep one repair retry for
  safety, but "the local model won't emit valid JSON" is no longer a real risk.
- **The pose layer must be a first-class op target.** Asked to make her "smiling warmly",
  gemma3 correctly aimed at `pose/expression`. If that isn't a valid destination, the
  model gets rejected for obeying the identity/expression rule.
- **The validator is the load-bearing component**, not the prompt. Every model tested
  needed the same five accommodations (bracketed ids, invented slot names, slot-name
  prefixes leaking into text, no-op replaces, stringified numbers). Before they were in,
  the model ranking was simply wrong.

---

## 10. Risks

1. ~~**Small-model JSON reliability.**~~ **Retired by measurement** (§9): schema-constrained
   decoding gave 7/7 valid JSON on every installed model. Keep one repair retry as a belt,
   but this is no longer the thing to worry about. The live risk moved to *semantic*
   quality — models emit valid JSON that says the wrong thing (4/7 to 6/7 correct
   depending on model), so per-op accept/reject is doing real work, not decoration.
2. **Paren escaping.** The classic footgun. One unescaped `(` in a user phrase corrupts
   every weight after it. Compiler-level, covered by tests, not left to the model.
3. **Over-structuring.** If slot classification feels like admin, users will fight it.
   Mitigated by an always-available `misc`, drag-to-reslot, and the text escape hatch.
4. **`comic` is the weakest preset** — the label covers three unrelated looks. Ship one,
   make cloning easy, expect the user to fork it.
