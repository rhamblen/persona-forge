# Emotional Depth — design doc (Phase H)

> Progressive emotional state in SillyTavern, and the LoRA/dataset machinery that
> renders it. Written 2026-07-26 from the "can we progress emotional states in ST"
> discussion. Design doc — nothing here is built yet.

Two halves, deliberately separable:

- **H1 — Emotion-targeted enrichment (tactical, needed soon).** Hone a LoRA on one
  emotion at a time and rebuild only that emotion's sprites, without losing the
  baseline. Lives entirely inside Persona Forge; extends Phases B/C/D.
- **H2 — The emotion state engine (big picture, post-1.0).** Emotion as *game state*
  with memory and progression, exported as a driver SillyTavern actually runs.

H1 ships first and stands alone: a richer sprite set is worth having even if H2 never
lands. H2 is what makes the extra sprites *mean* something.

---

## 0. The reframe

Emotion in this pipeline is **two layers**, and almost every design mistake comes from
collapsing them:

| Layer | What it is | Where it lives |
|---|---|---|
| **State** | The character's internal condition — `anger 62, trust 85` | The engine (H2) |
| **Sprite** | A *projection* of that state onto a picture | The asset set (H1) |

SillyTavern today has no state layer. Its Expressions extension is a **stateless label
lookup**: the LLM emits a label, the extension loads `<label>.png`. There is no anger
meter, no memory, no progression — every message is classified independently.

So: the state layer must be built, and the sprite layer must have enough resolution to
render it. H1 builds resolution; H2 builds state.

---

## 1. Axes and tiers — the shared vocabulary

Emotion is **two dimensions**, not one:

- **Axis** — *which* emotion (anger, fear, sadness…).
- **Tier** — *how much* of it (annoyance → anger → fury → rage).

**The happy accident: the 28 SillyTavern labels already encode tiers.** They are the
GoEmotions set, and several axes come pre-graded. Grouping them by axis and ordering by
intensity yields most of the ladder for free — only the top tiers are missing:

| Axis | T1 | T2 | T3 | T4 (new, custom) |
|---|---|---|---|---|
| **anger** | annoyance | anger | — | **fury** |
| **fear** | nervousness | fear | — | **terror** |
| **sadness** | disappointment | sadness | grief | **despair** |
| **joy** | amusement | joy | excitement | **elation** |
| **affection** | approval | caring | love / admiration | **devotion** |
| **disgust** | disapproval | disgust | — | **revulsion** |
| **shame** | embarrassment | remorse | — | **humiliation** |
| **interest** | curiosity | realization | surprise | — |
| **calm** | neutral | relief | optimism / pride | — |
| *(unassigned)* | confusion, desire, gratitude, disappointment… | | | |

Consequences:

- **The 28 stay the baseline and the export target.** Nothing about today's Phase D
  changes; tiers are *additive*.
- **A full ladder is ~6–8 new sprites**, not a new set of 28. Cheap.
- The new labels are **custom ST expressions**. ST supports adding custom expression
  labels, but the mechanism has moved across versions — **verify against the running ST
  at build time**, same discipline as the V3 card decision.
- `EXPRESSIONS_28` in `backend/app/main.py:1538` becomes the flat projection of a
  structured axis map, not the source of truth. Ship the table above as the default,
  **editable per project** (a stoic character may not need a rage tier at all).

---

## 2. H1 — Emotion-targeted enrichment

> The user's framing: *"the basic 28 startup, then focus the dataset on a specific
> emotion where we enrich and grow, but can come back to the baseline for a different
> emotion."*

### 2.1 The core idea: dataset **layers**

"Come back to baseline" must be a **selection, not an undo**. So the dataset stops being
one flat pile and becomes named layers:

```
core            ← the Phase B dataset. Immutable once signed off.
emotion:anger   ← enrichment shots honing the anger axis
emotion:grief   ← honing the sadness axis
…
```

A LoRA build declares **which layers it trains on**:

| Build | Layers | Purpose |
|---|---|---|
| v1 | `core` | the baseline — always reproducible |
| v2 | `core + anger` | anger honed |
| v3 | `core + anger + grief` | cumulative (default) |
| v3′ | `core + grief` | reset to baseline, hone a different axis |

Every build **trains from scratch on the union of its layers** — never continued
training on top of v2. Continued training compounds drift; from-scratch keeps every
build a clean function of `(layers, recipe)` and makes v1 exactly reproducible forever.
Training is ~1 hr, so this costs time, not correctness — and the dedicated-training-GPU
plan (PROJECT_PLAN §7) is what makes it comfortable.

### 2.2 The baseline gate — no focused build before a basic build

**Hard sequencing rule (user, 2026-07-26): you don't hone an emotion until the basic
emotion set exists.** Enrichment is a *correction* applied to something you have already
looked at — without the baseline you're guessing at which axis is weak, and you'd be
generating enrichment data from an unproven LoRA.

So the enrichment UI is **gated on baseline readiness**:

- LoRA v1 trained from a signed-off `core` dataset, **and**
- the **28 baseline sprites rendered** (`poses.status='done'`) and reviewed.

Until both hold, the Hone panel is disabled and says why. Once they hold, the review of
that baseline set *is* the input to the next decision: you pick the axis to hone because
you can see it's the weak one.

This also means the axis map (§1) has a job before any enrichment exists: it's what turns
"the 28 look uneven" into "the anger axis is weak," by grouping the baseline grid by axis
rather than listing it alphabetically.

### 2.3 The enrichment loop

```
  core dataset ──► LoRA v1 ──► 28 baseline sprites ──► REVIEW  ◄── the gate (§2.2)
                                                          │
                                          pick the weak axis (e.g. anger)
                                                          ▼
             generate enrichment batch
             = character LoRA v1  +  a stacked pose/emotion concept LoRA  (§2.4)
               × tier prompts × framings
                                                          │
                                    human curation  ← the quality gate, non-negotiable
                                                          ▼
                                              layer 'emotion:anger'
                                                          │
                                                          ▼
                                          LoRA v2 = train(core + anger)
                                                          │
                                                          ▼
        rebuild ONLY the anger-axis sprites ──► compare vs v1 ──► accept / roll back
                                                          │
                                                          └─► next axis starts from core
```

### 2.4 The LoRA stack — overlay tools for poses and emotions

> User, 2026-07-26: *"the prompt build needs a secondary tool where we might want to
> overlay different LoRA tools for the poses and emotions. I've seen LoRAs which focus on
> arm movement or sitting positions which enrich the base model."*

This is the piece that makes the whole loop safe, and it deserves to be a first-class
feature rather than an enrichment detail.

**Two kinds of LoRA, different jobs:**

| | **Character LoRA** | **Concept LoRA** (pose / gesture / emotion) |
|---|---|---|
| Source | trained here, per character | third-party or reused across characters |
| Carries | *who* — identity | *what the body is doing* |
| Count | exactly one | zero to a few, stacked |
| Weight | ~0.8–1.0 | ~0.4–0.8 |

**Why this matters more than it looks: it breaks the self-feed loop.** §2.6 flags the real
risk that training LoRA v2 on LoRA v1's own output amplifies its flaws. A concept LoRA is
**external signal** — it knows arm positions and sitting poses the character LoRA has never
seen. So the pattern is:

> **Teacher LoRA.** Stack a concept LoRA to *generate* enrichment shots the character LoRA
> couldn't produce alone → curate → train v2 on them → the character LoRA now knows those
> poses natively → **drop the concept LoRA at render time.** It's scaffolding you remove.

That converts enrichment from "recycle my own output" into "absorb a capability," which is
a genuinely different and much stronger proposition.

**What the secondary tool is.** A **LoRA stack editor** in the Prompt/Pose studios: add N
LoRAs, reorder, set per-LoRA model/CLIP strength, toggle each on/off, and see the resulting
prompt (trigger words appended automatically). Saved per version, so it rolls back with
everything else.

**A small LoRA library** backs it — an installed concept LoRA records: display name, file,
**base-model compatibility** (an SD1.5 pose LoRA will not work on the NoobAI-XL checkpoint —
this must be checked, not assumed), **trigger words**, recommended weight range, and a
category (`pose` / `gesture` / `expression` / `style`). Categories let the enrichment builder
suggest *"you're honing anger — these pose LoRAs are relevant."*

**Implementation.** `base-character-lora.json` already carries a single `LoraLoader`
(model + CLIP) for one external style LoRA. Generalise it to a **chain of `LoraLoader`
nodes spliced programmatically** — one per stack entry, model+CLIP threaded through.
Deliberately **core nodes only**: a "power LoRA loader" custom node would be tidier, but
`custom_nodes` is read-only over SMB on UR1 and the flash_attn breakage already cost a
day. Chained core loaders need nothing installed.

**Cautions.** Stacked LoRAs fight — each one pulls the model, and identity is what loses.
Cap the stack at ~2–3, keep the character LoRA highest, and treat a concept LoRA that
visibly changes the face as incompatible regardless of how good the pose is. Curation
catches this, but it's worth saying out loud.

### 2.5 What the enrichment shots must contain

This is the part face-only repainting cannot do. "Rage" and "despair" are **not face
changes** — they are posture collapse, clenched fists, a body lunging forward, tears,
sweat, dishevelment. The current Live2D-style FaceDetailer approach repaints only the
face; that's why the LoRA has to learn the *body language*.

So an enrichment batch is **tier prompts × framings**, mirroring `_dataset_variation()`
(`main.py:899`) with a new `mode="emotion"`:

```python
EMOTION_ENRICHMENT = {
  "anger": [
    "annoyed, slight frown, arms loosely folded, weight on one hip",
    "angry, furrowed brow, jaw set, shoulders squared, fists at sides",
    "furious, shouting, leaning forward aggressively, one fist clenched",
    "enraged, screaming, whole body tensed, arms flung wide",
  ],
  ...
}
```

crossed with the **full framing spread** (close-up → bust → cowboy → full body) so the
model learns the emotion at every scale, not just as a face.

**Caption rule (critical).** Enrichment captions must name the emotion *explicitly*.
The project's standing rule keeps expression words **out of the identity/trigger token**
— that rule is exactly why this works: caption the emotion and it binds to the emotion
words as a separable, promptable concept; omit it and the emotion gets absorbed into the
trigger word and the character starts looking angry at rest.

### 2.6 Guard rails

Feeding LoRA-v1 output back in as LoRA-v2 training data is a real self-amplification
risk (mode collapse, baked-in flaws). Five mitigations, all cheap:

1. **Prefer external signal** — a stacked concept LoRA (§2.4), ControlNet-posed shots, or
   real reference imagery. This is the strongest mitigation by far, because it adds
   information rather than recycling it. Treat pure self-feed as the fallback.
2. **Core stays ≥ 50%** of any training set. `core` is never edited or deleted.
3. **A single emotion layer caps at ~25–30%** of the total. Enough to teach a concept,
   not enough to redefine the character.
4. **Human curation is mandatory** — the existing Phase B selectable-grid gate, reused.
   Reject anything off-model *before* it becomes training data.
5. **Generate enrichment at reduced character-LoRA weight** (~0.7–0.8) so the batch
   carries variety rather than echoing v1 exactly — and so a stacked concept LoRA has
   room to act.

### 2.7 Selective sprite rebuild

The `poses` table (`db.py:95`) already regenerates per-row, so this is mostly
bookkeeping. Add `axis`, `tier`, and `lora_build_id` to each pose, and:

- **Rebuild by axis** — after LoRA v2, re-render only `WHERE axis='anger'`. The other 24
  sprites are untouched and still valid.
- **Stale indicator** — any sprite whose `lora_build_id` ≠ the active build shows as
  stale in the grid, with a one-click rebuild. This makes the whole loop legible: enrich
  anger → four sprites go stale → rebuild → compare.
- **Keep the previous render.** Same rollback ethos as prompts: an enrichment that made
  things worse must be revertable per sprite, not just per LoRA.

### 2.8 Schema deltas

```sql
ALTER TABLE images ADD COLUMN layer TEXT NOT NULL DEFAULT 'core';
                        -- 'core' | 'emotion:<axis>'

CREATE TABLE lora_builds (
  id, project_id, version_no,
  layers_json,        -- ["core","emotion:anger"]
  steps, rank, recipe_json,
  lora_filename, active, created_at
);

-- §2.4: the concept-LoRA library (shared across projects, not per character)
CREATE TABLE concept_loras (
  id, name, filename,
  base_model,         -- 'noobai-xl' | 'sdxl' | ... — compatibility, must be checked
  category,           -- 'pose' | 'gesture' | 'expression' | 'style'
  trigger_words, weight_min, weight_max, notes
);

-- the per-version stack: which concept LoRAs are overlaid, in order, at what strength
CREATE TABLE lora_stack (
  id, version_id, position, concept_lora_id,
  strength_model, strength_clip, enabled
);

ALTER TABLE poses ADD COLUMN axis           TEXT NOT NULL DEFAULT '';
ALTER TABLE poses ADD COLUMN tier           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE poses ADD COLUMN lora_build_id  INTEGER;   -- provenance → staleness
```

The `lora_build` job handler (`jobs.py`) gains a `layers` param and an `axes` param
("train, then re-render only these axes"), which is a small change to an existing
orchestrator rather than a new pipeline.

### 2.9 Overlap with existing backlog

The roadmap's **"custom / editable dataset example prompts"** item (§7, 0.7.x backlog)
is the same machinery viewed from a different angle: a user-editable list of extra
dataset shots. Emotion enrichment is that feature with an **axis label attached** so the
shots can be grouped, capped, and selectively rebuilt. **Build them together** — one
mechanism, two entry points.

---

## 3. H2 — The emotion state engine (post-1.0)

### 3.1 Model

```json
{ "anger": 42, "fear": 10, "joy": 68, "trust": 55, "arousal": 30 }
```

Each axis 0–100, bucketed to a tier by the §1 table, and the **displayed sprite is
derived** from the dominant axis — the engine never asks the LLM "what face are you?"

The payoff is that state is richer than the sprite. `trust 85 / anger 70` and
`trust 20 / anger 70` render the *same furious sprite* but produce different dialogue —
*"You're an idiot! But you're still my friend"* vs. *"Get out. We're done."*

### 3.2 LLM proposes, engine remembers

Don't sentiment-guess the text, and don't let the LLM own the numbers. Split it:

- The **LLM emits deltas** it can justify from the scene:
  `<mood anger="+20" trust="-15"/>`
- The **engine owns the state** — applies deltas, clamps, decays, persists, and picks
  the sprite.

The LLM is good at judging *what just happened*; it is bad at maintaining a consistent
counter across 200 messages. This split plays to both.

### 3.3 Temperament — the missing character field

State needs a **baseline to return to**, and it's per-character:

- **Resting values** — where each axis sits at rest.
- **Volatility** — how hard deltas hit (a hair-trigger vs. a stoic).
- **Decay rate** — how fast it returns to rest. A grudge-holder decays anger slowly.
- **Ceilings** — some characters never reach rage.

This is a natural new block on the **Phase E character sheet**, drafted by Ollama from
the same seed and checked by the coherence pass. It also feeds §1: a character whose
anger ceiling is 60 doesn't need a rage sprite, so the **sheet decides which tiers get
generated** — temperament drives the asset list.

### 3.4 Running it in SillyTavern

ST can do this today with built-ins — no forked extension required:

- **Variables** (`/setvar`, `/getvar`, `/addvar`) hold the scores.
- **Quick Replies with auto-execute after each AI message** run the update script: parse
  the `<mood>` tag, apply deltas + decay, bucket, force the sprite.
- **Forcing the sprite** bypasses the built-in classifier entirely. *(The Expressions
  extension's slash command for this has changed name across versions — verify against
  the running ST.)*
- **Author's Note / lorebook injection** feeds current state back into the prompt so the
  *dialogue* reflects it too.

**Persona Forge's job is to author that bundle**, not to run it: the character export
gains the axis map, the temperament block, the update rules, and the generated
STScript/Quick-Reply set — staged for manual import, exactly like sprites and cards.
That keeps emotion design where the tooling and NL editing already live.

### 3.5 Honest limits

- Deltas are only as good as the LLM's judgement; expect tuning.
- STScript is a real but awkward programming surface — this will be the fiddliest export.
- Weak local models may drop the `<mood>` tag. Need a fallback (no tag → decay only).
- Every axis added multiplies prompt overhead. **Start with 4–5 axes.**

---

## 4. Sequencing

Ordered by dependency — each stage is usable on its own, and the early ones pay off even
if enrichment never ships.

| Stage | Scope | When |
|---|---|---|
| **H1a** | Axis/tier map + custom tier labels, editable per project; **baseline grid grouped by axis** so weakness is visible (§2.2) | next phase |
| **H1b** | **LoRA stack + concept-LoRA library** (§2.4) — chained core `LoraLoader`s, per-entry weights, trigger words, compatibility. *Independently useful: better poses from day one, no enrichment required.* | ✅ **shipped 0.8.0** |
| **H1c** | Dataset layers + `mode="emotion"` enrichment batches, behind the **baseline gate** (§2.2). Absorbs the editable-examples backlog item. | next phase |
| **H1d** | `lora_builds` versioning + layer-selected from-scratch training + rollback | next phase |
| **H1e** | Per-axis selective sprite rebuild + staleness + per-sprite revert | next phase |
| **H2a** | Temperament block on the Phase E character sheet | with Phase E |
| **H2b** | State model + delta protocol + STScript bundle export | post-1.0 |

**H1b is the one to build first if only one gets built.** The LoRA stack improves every
generation immediately — baseline sprites included — and it's the prerequisite that makes
enrichment (H1c) add external information instead of recycling the model's own output.

**Recommended numbering.** H1 is close to shipping code (it extends Phases B/C/D, which
exist) and the user needs it sooner than Character Studio — so **H1 → `0.8.x`, Character
Studio → `0.9.x`, 1.0 unchanged**. H2b sits post-1.0 near Phase F. *Roadmap
resequencing — user's call.*

## 5. Does this displace the VRM track?

> User question, 2026-07-26: *"this approach may eliminate the need for the VRM because
> it might be more immersive."*

**Largely yes — and for a specific reason: emotional range was never VRM's strength.**

A stock VRM rig ships the standard blendshape set — roughly *joy / angry / sorrow / fun /
surprised* plus visemes and blinks. That's **~5 emotion shapes against the 28+ this
pipeline already produces**, and the graded tiers push it to 34+. Getting a VRM to
express *fury vs. annoyance*, or a posture collapse into despair, means authoring custom
blendshapes **and** animation clips **per character, per emotion** — strictly more work
than generating a sprite, and it lands in a real-time toon shader instead of a full
diffusion render. On the axis the user actually cares about — emotional legibility —
**enriched sprites beat a VRM outright**, and they beat it at a fraction of the cost.

What VRM still uniquely owns:

- **Continuity** — smooth blending between states instead of a sprite pop.
- **Liveness** — idle breathing, blinking, head/gaze tracking toward the user.
- **Lip sync** — genuinely valuable, and non-hypothetical given chatterbox TTS is
  already on the box.
- **3D presence** — camera moves, turning to face another character.

Two of those four are partly stealable on the sprite side: **crossfade between sprites**
softens the pop, and a subtle idle bob/breathing animation buys liveness cheaply. That
leaves **lip sync** as VRM's one durable advantage — so the honest test is: *does this
project want a talking avatar, or an expressive one?* For text-driven solo D&D in ST,
it's the latter.

There's also an argument that tiers are **more** narratively legible than a 3D blend:
watching a character step annoyance → anger → fury → rage reads as *progression* in a
way a continuous morph does not. Discrete states are what make the escalation visible.

**Recommendation: demote VRM from "v2 for bipedal heroes" to opt-in/experimental, and
spend that effort on H1 instead.** Don't kill the track — the vrm-viewer and Blender MCP
work already exists and lip-sync may matter later — but it stops being the planned next
step for hero characters. This also collapses the humanoid/non-humanoid split in the
avatar strategy: **one sprite pipeline serves the entire cast**, monsters included, at
one quality bar. *Strategic call — needs the user's confirmation before the parent
`PROJECT_PLAN.md` and the avatar-strategy memory are rewritten.*

## 6. Open decisions

- **Cumulative vs. reset default** — does honing grief keep the anger layer in (v3) or
  start from core (v3′)? Recommendation: **cumulative by default**, layers deselectable.
- **Concept-LoRA sourcing** — which pose/gesture LoRAs to install for the NoobAI-XL
  checkpoint, and whether the library ships a curated starter set or stays
  bring-your-own. Compatibility is the gating question, not quality.
- **Does the stack ever stay on at render time?** The teacher-LoRA pattern drops it after
  training, but for a pose the character LoRA never absorbs, keeping it stacked during
  sprite render is legitimate. Recommendation: allow it, record it on the pose so the
  render is reproducible.
- **Layer cap enforcement** — hard cap at 30% or just warn? Recommendation: warn, and
  show the ratio in the training UI.
- **Custom ST expression labels** — exact mechanism on the running ST version, and
  whether tiers ship as custom labels or as a separate sprite folder the driver picks
  from.
- **Axis count for H2** — 4–5 to start; which? (anger / fear / joy / sadness / trust is
  the natural first set, `arousal` optional.)
- **Where temperament lives** — character-sheet field vs. a separate emotion profile
  attached to the project.
