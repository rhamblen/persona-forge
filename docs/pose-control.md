# Structural pose control — design doc (Phase H3)

> A ControlNet pose library that drives *body structure* from a skeleton instead of
> asking the character LoRA to infer it from prose. Written 2026-07-28 from the "the
> poses are very similar" observation. **Design doc — nothing here is built yet.**
> Companion to [`emotion-depth.md`](emotion-depth.md), which already names
> ControlNet-posed shots as the strongest source of *external signal* (§2.6).

---

## 0. Why the poses are similar — three causes, not one

Worth separating, because one of them is a one-line fix and only one of them needs
ControlNet.

**(a) Every pose renders at the same seed.** `_queue_pose` (`main.py:2160`) passes
`"seed": version.get("seed") or 0` for all 35 tiers. Same checkpoint, same character
prompt, same LoRA, same seed — the only difference is a short expression suffix. Diffusion
is deterministic in the seed, so the composition is *supposed* to come out nearly
identical. This alone explains a large share of the sameness and costs one change to fix.

**(b) Prose posture is weakly obeyed.** The 0.8.2 modifiers already describe the body —
*"leaning forward aggressively, one fist clenched and raised"* — but an SDXL/Illustrious
anime checkpoint spends most of its conditioning budget on subject and style, and the
character LoRA actively pulls toward the mean pose of its training set. Whatever the
dataset over-represented (usually a front-facing standing shot) is what comes back.

**(c) There is no structural conditioning at all.** Nothing in the pipeline constrains
where the limbs are. Prompt text is the only lever, and it is the wrong kind of lever.

So the plan is: **fix (a) as part of the work, and solve (c) with an OpenPose ControlNet
pose library.** (b) then stops mattering — the prose becomes flavour on top of a structure
that is already fixed.

---

## 1. What's already on the box — verified live, 2026-07-28

Checked against `http://192.168.1.33:9000/object_info` (ComfyUI 0.28.0, 2916 nodes).

**Present and usable:**

| Node | Pack | Why it matters |
|---|---|---|
| `ControlNetLoader`, `ControlNetApplyAdvanced` | core | apply, with `strength` + `start_percent` / `end_percent` |
| `SetUnionControlNetType` | core | needed only if a union CN is chosen |
| `DWPreprocessor` | `comfyui_controlnet_aux` | image → **skeleton IMAGE + `POSE_KEYPOINT`**; the good detector |
| `OpenposePreprocessor` | `comfyui_controlnet_aux` | older detector, same two outputs |
| `AnimalPosePreprocessor`, `RenderAnimalKps` | `comfyui_controlnet_aux` | the non-humanoid cast, experimentally |
| `SavePoseKpsAsJsonFile` | `comfyui_controlnet_aux` | writes keypoints to the output dir → readable over the shared mount |
| `RenderPeopleKps`, `SDPoseDrawKeypoints` | aux / `comfy_extras` | keypoints → skeleton image |

**Two findings that shape the design:**

1. **There is no "load pose keypoints from JSON" node.** Keypoints can be *saved* but not
   *loaded* back into a graph. → the skeleton PNG is what gets fed to ControlNet, and
   Persona Forge renders that PNG itself from stored keypoints. Which is the right split
   anyway: **keypoint JSON is the source of truth in the DB, the PNG is a derived
   artifact**, and the drawing code is ~60 lines of PIL over 18 COCO points.
2. **`easy poseEditor` (ComfyUI-Easy-Use) is UI-only** — it declares no outputs, so it
   cannot appear in an API-submitted graph. The eventual stickman editor has to be built
   in Persona Forge's own frontend (canvas + draggable joints). Not a loss: an editor
   inside the app is what the user asked for, and it can write straight to the library.

**The one real blocker — no SDXL ControlNet is installed.** `ControlNetLoader` offers
only the SD1.5 v1.1 set and three FLUX models. `control_v11p_sd15_openpose.pth` will not
load against an SDXL/Illustrious checkpoint. **A model download is a hard prerequisite**
(§2).

---

## 2. Prerequisite — install an OpenPose ControlNet (user action)

Target directory, confirmed present:

```
\\192.168.1.33\appdata\stable-diffusion\models\controlnet\
```

That is the shared A1111/ComfyUI models tree — the same tree whose `models\stable-diffusion\`
is writable from Windows, so `curl.exe -L` straight into it is the route. ComfyUI-Manager
3.x whitelist-gates arbitrary-URL installs and Civitai is region-blocked from UR1;
**Hugging Face is not blocked** (memory: `project_comfyui_ur1_paths_and_env`). Restart
ComfyUI afterwards via the Unraid MCP, then the file appears in `ControlNetLoader`.

**Installed 2026-07-28** (user authorised; both fp16, 2.5 GB each):

| File in `models\controlnet\` | Source | Fits |
|---|---|---|
| `noobai-openpose-sdxl.safetensors` | `Laxhar/noob_openpose` → `openpose_pre.safetensors` | **NoobAI-XL / Illustrious** — the official Laxhar (NoobAI team) openpose CN, native to the current default checkpoint. First choice. |
| `xinsir-openpose-sdxl-1.0.safetensors` | `xinsir/controlnet-openpose-sdxl-1.0` | **any SDXL** — the strongest generic SDXL openpose CN. The fallback that keeps the project checkpoint-neutral. |

Deliberately two, not one: the registry's `base_model` field is what picks between them, so
moving the persona to a non-NoobAI SDXL checkpoint doesn't strand pose control.

**Both VALIDATED live, 2026-07-28.** ComfyUI picked the files up **without a restart** (the
folder cache invalidates on directory mtime). Each was then run through a real 4-step
`CheckpointLoaderSimple → ControlNetLoader → ControlNetApplyAdvanced → KSampler` graph
against `animi/NoobAI-XL-v1.1.safetensors` at `strength 0.7, end_percent 0.7` — both
returned `success`. This mattered for the xinsir file specifically: it ships in **diffusers**
format, and "appears in the dropdown" is not evidence that ComfyUI can convert its state
dict. It can. Probe images were deleted; nothing was left on the share.

Not taken (available if wanted later): `xinsir/controlnet-union-sdxl-1.0` ProMax — one file,
many modes via `SetUnionControlNetType`, but weaker per-mode than a dedicated openpose CN;
and the SD1.5 v1.1 set already on disk, usable only if a persona ever runs an SD1.5
checkpoint.

**This is exactly the concept-LoRA compatibility problem again** (0.8.0/0.8.1): a CN is
bound to a checkpoint family. So ControlNets get the same treatment — a small registry
with a `base_model` field, and a pre-flight check that names the missing file instead of
letting ComfyUI throw a node-level error. Nothing gets pinned to one checkpoint family.

---

## 3. The model

### 3.1 Pose library — global, not per persona

Like the concept-LoRA library, and for the same reason: a skeleton is character-agnostic.
Register a pose once, use it on every persona.

```sql
CREATE TABLE pose_library (
  id, name, category,          -- 'standing' | 'sitting' | 'kneeling' | 'arms' | 'hands' | 'head' | 'monster'
  framing,                     -- 'bust' | 'cowboy' | 'full'  — see §5, this is the trap
  keypoints_json,              -- COCO-18 body (+ optional hands/face), normalised 0..1
  canvas_w, canvas_h,          -- what the keypoints were authored against
  skeleton_file,               -- cached rendered PNG (derived, regenerable)
  source,                      -- 'builtin' | 'imported' | 'harvested' | 'edited'
  parent_id,                   -- lineage when edited from another entry
  tags, notes, created_at
);
```

Normalised keypoints mean a library entry can be re-rendered at any target resolution —
essential, because a skeleton at the wrong aspect squashes the pose.

### 3.2 Bindings — the user's "a few options, randomly selected"

Each emotion tier points at a **group** of candidate poses rather than one:

```sql
CREATE TABLE pose_bindings (
  id, scope,                   -- 'default' (ships with the app) | 'project'
  project_id,                  -- NULL for defaults
  axis, tier,                  -- resolves against the editable emotion map, by name
  pose_id, weight, created_at
);
```

`anger/fury → [aggressive_lean, fist_raised, shouting_arms_wide]`, and the render picks
one. Per-persona rows override the defaults for that persona, exactly like the emotion map
is a default that any project can diverge from.

### 3.3 Provenance on each pose

```sql
ALTER TABLE poses ADD COLUMN pose_id      INTEGER;  -- which skeleton produced this render
ALTER TABLE poses ADD COLUMN pose_pinned  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE poses ADD COLUMN cn_strength  REAL    NOT NULL DEFAULT 0.7;
ALTER TABLE poses ADD COLUMN seed         INTEGER NOT NULL DEFAULT 0;  -- fixes §0(a)
```

**Selection happens once and is recorded, not re-rolled on every run.** Random-at-render
would mean regenerating a single sprite silently changes it — the opposite of the
reproducibility every other part of this app defends. So: first generate picks from the
group and *writes it down*; re-rolling is a deliberate 🎲 click; pinning stops it changing.

### 3.4 The seed fix, riding along

Per-pose seed defaults to `hash(version.seed, pose.id)` — deterministic and reproducible,
but different per tier, so two sprites are no longer the same picture with a different
mouth. Stored on the row so a good render can be reproduced exactly.

---

### 3.5 Full body, and the starter library

**Decided (user, 2026-07-28): sprites are full body.** That settles what the library is
made of — every entry is a whole-figure skeleton, and `framing` stops being a filter that
excludes most of the set and becomes a record of what the skeleton actually covers.

Starter set, standing first because it is the workhorse:

| Category | Entries |
|---|---|
| **standing** | neutral stance · weight on one hip · arms crossed · hands on hips · arms raised overhead · shrugging · head down · covering face · fists clenched at sides · arms flung wide |
| **grounded** | kneeling upright · kneeling slumped · sitting on the floor, legs to one side · sitting hugging knees · lying down |
| **props** | see §3.6 |

These map straight onto the emotion tiers that already exist: `despair` wants *kneeling
slumped* or *sitting hugging knees*, `elation` wants *arms flung wide*, `humiliation` wants
*covering face*, `pride` wants *hands on hips*. So the default bindings (§3.2) largely write
themselves from the 0.8.2 modifier prose.

### 3.6 Props — poses that hold something

> User, 2026-07-28: *"what if the character is holding something like a book or a sword?"*

A skeleton encodes **arm and hand configuration, not the object**. Pose a figure as if
gripping a sword and prompt nothing, and you get the classic ControlNet artifact: a
convincing two-handed grip closed around thin air. The object has to come from the prompt,
in sync with the skeleton.

So a library entry gains two optional fields:

```sql
ALTER TABLE pose_library ADD COLUMN prompt_hint TEXT NOT NULL DEFAULT '';
        -- 'holding a sword in both hands, blade angled down'
ALTER TABLE pose_library ADD COLUMN prop_slot   TEXT NOT NULL DEFAULT '';
        -- 'book' | 'sword' | '' — what kind of object the grip expects
```

`prompt_hint` is appended to the render prompt whenever that skeleton is used — the same
mechanism as concept-LoRA trigger words in 0.8.0, which already appends and de-duplicates.
`prop_slot` makes the grip **reusable across objects**: one two-handed-grip skeleton serves
sword, staff, or broom by swapping the hint, instead of needing a skeleton per object.

**Two cautions, both real:**

1. **Hands matter more here than anywhere else.** A grip needs hand keypoints, and DWPose
   hands are the noisiest part of the detection. Prop entries should carry hand keypoints
   (`detect_hand` on at import) and expect curation; body-only entries are fine without.
2. **Props must not contaminate the character LoRA.** This is precisely the failure the
   project already proved with expressions — a smile baked into the character field leaks
   into `anger` and `grief`. A dataset heavy with sword shots teaches the LoRA that the
   sword *is part of the character*, and it starts appearing unbidden. Same rule, same
   reason: **caption the prop explicitly** so it binds to the prop words as a separable
   concept, and keep prop shots a minority of any training layer. The safest default is to
   keep props **out of the `core` dataset layer** entirely and treat them as a pose-render
   feature, promoting them into training only deliberately.

## 4. Workflows

**`pose-with-lora-cn.json`** — `pose-with-lora` plus three nodes:

```
LoadImage(skeleton) ─┐
ControlNetLoader ────┴─► ControlNetApplyAdvanced ─► KSampler
                          ▲ positive/negative from the two CLIPTextEncodes
```

ControlNet touches **conditioning only**, and the H1b concept-LoRA chain touches
**model/CLIP only** — they are orthogonal, so the existing `lora_chain` inject shape in
the manifest keeps working untouched. New manifest params: `pose_image`,
`controlnet_name`, `cn_strength`, `cn_start`, `cn_end`.

The skeleton PNG reaches ComfyUI through `comfy.upload_image()` → `/upload/image`, the
same staging path the dataset already uses (`main.py:1843`). No new mount, no shared-folder
dependency.

### 4.0 H3a calibration — measured against the live box, 2026-07-28

Run against persona `sweetie-pie` with its real trained character LoRA, one
`STANDING_NEUTRAL` skeleton, and the emotion prose for `anger/fury`.

**Pass 1 (ControlNet) works, first try.** Full figure, feet planted, head framed, arms
tracking the skeleton. 85 s at 832×1216 / 28 steps. The xinsir CN was the correct pick
here — this persona's version pins `!first/sd_xl_base_1.0`, not NoobAI, which is exactly
the case the two-model registry exists for.

**And it immediately proved the bindings argument (§3.2).** The prompt asked for *"furious,
shouting, leaning forward aggressively, one fist clenched and raised"* and returned a calm,
faintly smiling woman standing at rest. **The skeleton wins outright over posture prose.**
You cannot pose a neutral skeleton and prompt fury — so the per-tier skeleton binding isn't
a convenience, it's the only thing that makes an emotion's *body* happen.

**Pass 2 (FaceDetailer) — the denoise sweep first read as a failure, and wasn't.**
At 0.45 / 0.60 / 0.75 on the version's own base-SDXL checkpoint, every result was the same
flat, serious face. Varying prompt order and dropping the character LoRA to 0.5 changed
nothing. Re-run against `animi/NoobAI-XL-v1.1` it produced a genuinely furious, shouting
face at 0.60 — and a grotesque one at 0.75, jaw disintegrating, identity gone.

**That comparison changed two variables at once** (checkpoint *and* character LoRA), so it
did not actually establish the cause — a LoRA trained on an all-neutral dataset with
trigger-word-only captions would bind a neutral face to the trigger and produce the same
symptom. Completing the 2×2 at fixed denoise 0.60, expression-first prompt, seed 7:

| | **no character LoRA** | **character LoRA @ 1.0** |
|---|---|---|
| **base SDXL** | flat, faint frown | flat, neutral |
| **NoobAI-XL** | furious, shouting | **furious, shouting — identity intact** |

**The checkpoint is the determining variable; the LoRA is exonerated.** It emotes fine at
full strength — the character LoRA is not suppressing expression, and no retrain is needed
to fix this. (Note the LoRA was trained against a different SDXL base and still applies
cleanly on NoobAI; SDXL-family LoRAs cross over well enough for this.)

So the two-pass model does what it was designed to do — **body from ControlNet, emotion
from the face pass, independently tunable** — and it lands on the same number Track A
found the hard way: *0.6 controls expression, past 0.7 identity drifts*. Two independent
routes to the same threshold is about as much confidence as this kind of setting gets.

**Settled from this:**
- **Face-pass denoise defaults to 0.60**, with the UI warning above ~0.70 rather than
  silently allowing the cliff.
- **The face pass is the emotion driver, not a sharpening pass.** 0.45 barely moves an
  expression; it is not a useful default.
- **An anime-capable checkpoint is a hard requirement for expressive sprites.** A persona
  pinned to base SDXL will render flat faces no matter how the dial is set — worth a UI
  warning, since the failure looks like a broken feature rather than a wrong checkpoint.
- FaceDetailer's `wildcard` input is **required with no schema default**; graph builders
  must send `""` or ComfyUI rejects the prompt at validation.

### 4.1 Two render passes, three tunable layers

> User, 2026-07-28: *"this implies there are 3 passes and you can tune any of the passes
> for each pose. base, face, body."*

The **control model is exactly that — three layers, independently tunable per pose.** The
mechanics are two render passes, and the difference is worth being precise about because it
decides what is cheap to re-try:

| Layer | What it is | Mechanically | Re-tuning it costs |
|---|---|---|---|
| **base** | who and how — prompt, character LoRA, seed, steps/cfg | pass 1 | full re-render |
| **body** | the skeleton, CN strength, `cn_start`/`cn_end` | **conditioning on pass 1**, not its own diffusion | full re-render |
| **face** | detailer denoise, the tier's prose, on/off | **pass 2** — crop, re-diffuse, paste back | **face pass only** |

Body is a *modifier on the base render*, not a third pass. Re-posing means re-rendering;
there is no way around that, because the skeleton has to be present while the figure is
being formed. (Rendering a base and then restructuring it img2img is possible and is worse
— more identity drift, slower, no benefit.)

**The asymmetry is the useful part, and it should drive the UI.** The face is the layer
most likely to need iteration — it carries the emotion, and its denoise is the dial nobody
gets right first time — and it is also the only layer that can be re-run **without losing a
body you liked**. So:

> **Keep the pre-detailer render.** `poses` stores `base_filename` (pass 1 output) alongside
> `filename` (final). A face re-roll re-runs pass 2 against the stored base image, taking
> seconds and leaving the pose untouched. Changing the skeleton or the seed re-renders both.

```sql
ALTER TABLE poses ADD COLUMN base_filename  TEXT NOT NULL DEFAULT '';  -- pre-detailer
ALTER TABLE poses ADD COLUMN face_pass      INTEGER NOT NULL DEFAULT 1;
ALTER TABLE poses ADD COLUMN face_denoise   REAL    NOT NULL DEFAULT 0.45;
```

This also gives the review loop a real before/after: the stored base is the pose *without*
the face pass, so the pass can be judged rather than assumed.

**Occluded faces — an edge case that hits the starter set directly.** `covering face`,
`head down`, and `lying down` are in §3.5, and all three either hide the face or turn it
away. FaceDetailer needs `bbox/face_yolov8m` to *find* a face; on these it will either
no-op or, worse, repaint a hand-over-face into something mangled. So library entries carry
a **`face_visible` flag**, and the face pass defaults **off** for entries where it's false —
overridable, but the default should not fight the pose.

**`pose-extract.json`** — `LoadImage → DWPreprocessor → (SaveImage + SavePoseKpsAsJsonFile)`.
Two entry points into the library:

- **Import from reference** — any image the user drops in becomes a library entry.
- **Harvest from a render** — the persona's own best sprite becomes a reusable skeleton.
  Cheap, and it turns a lucky render into a repeatable asset.

---

## 5. The traps, stated up front

- **Face fidelity is now the big one.** Full-body sprites (decided, §3.5) mean the head
  occupies a small fraction of the frame — at 1024², roughly a 100 px face. That is the
  exact problem Track A's 28-expression workflow solved by repainting *only* the face, and
  it collides head-on with the goal of a recognisable character. Two mitigations, both
  cheap and both worth doing: render **portrait aspect** (832×1216 or 896×1152) rather than
  square, and add an **optional FaceDetailer pass** to the pose graph — the same node
  already proven in `claude_live2d-28-expressions_v2_COUNTER`, run once after the CN
  render. Structure from ControlNet, identity from a face repaint, which is a clean split
  of responsibilities. **This should be settled in H3a**, because discovering it after a
  full sprite set is rendered wastes the set.
- **Framing consistency.** With everything full-body, the risk inverts: a skeleton whose
  figure sits at a different scale or position in frame than its neighbours produces a
  sprite set that jitters when SillyTavern swaps between them. Library entries should be
  authored against a **common canvas with the figure at a consistent scale and footing**,
  and the app should show the grid at sprite size so drift is visible.
- **ControlNet strength fights identity.** At 1.0 across the full step range the CN
  flattens style and pulls the face off-model. Default **0.7 with `end_percent ≈ 0.7`** —
  structure is decided in the early steps, then the last third is left to the character
  LoRA. Exposed per pose, because a subtle head-tilt wants less than a kneel.
- **Hands are unreliable.** DWPose hand keypoints are noisy and "clenched fists" is one of
  the harder asks. Detect body + face by default, hands opt-in per entry, and expect to
  curate.
- **Non-humanoid cast.** `AnimalPosePreprocessor` exists, but an openpose CN is trained on
  human topology. Monsters get a library category and an experimental label, not a promise.
- **Aspect/canvas** — always re-render the skeleton at the target render size from
  normalised keypoints. Never upload a stored PNG at the wrong dimensions.

---

## 6. Sequencing

Each stage is independently useful; the early ones pay off even if the editor never lands.

| Stage | Scope |
|---|---|
| **H3a** | **Prerequisite + render path.** CN registry with `base_model` + pre-flight guard; `pose-with-lora-cn.json`; **per-pose seed fix**; settle **portrait aspect + FaceDetailer** (§5). Prove one pose renders against one hand-made skeleton. |
| **H3b** | **Pose library.** Table + CRUD + the §3.5 starter set + `prompt_hint`/`prop_slot`; `pose-extract.json` for import-from-image and harvest-from-render. |
| **H3c** | **ControlNet in the dataset build** (§6.1) — the LoRA learns body structure, not just faces. |
| **H3d** | **Bindings + selection.** Tier → pose-group defaults per axis, recorded choice, 🎲 re-roll, 📌 pin, per-persona override. |
| **H3e** | **Pose studio page.** The "move from the LoRA to poses" route: review grid grouped by axis, per-pose CN strength, re-roll, compare against the previous render (previous kept, same rollback ethos as prompts). |
| **H3f** | **Stickman editor** *(later)*. Canvas keypoint editing in the frontend, saved as a derived library entry with lineage. |

**H3a is the one to build first if only one gets built** — the seed fix plus a single
skeleton will visibly break the sameness before any library exists.

### 6.1 ControlNet in the dataset build — the other half

> User, 2026-07-28: *"we can use ControlNet when building the initial poses for the LoRA,
> where we have different body positions as well as faces. Then when the LoRA is built,
> the poses allow us to use different ControlNet poses for the body position."*

This is the right instinct and it upgrades §6's ordering: CN is not a render-time garnish
bolted on at the end, it's **used twice, for two different jobs**.

| | **Dataset build** (H3c) | **Sprite render** (H3a) |
|---|---|---|
| Job | teach the LoRA what this character's body *does* | put the trained character in a specific pose |
| CN strength | moderate (~0.5–0.7) — variety, not rigidity | 0.7 with `end_percent` 0.7 |
| Skeleton source | a wide spread across the library | the tier's bound pose |

The dataset half is what durably fixes cause (b) in §0. A LoRA trained only on
front-facing standing shots has no idea what this character looks like kneeling, so *no*
amount of render-time ControlNet will produce a good kneel — CN will force the geometry
and the LoRA will fight it, which shows up as melted anatomy. **Teach it first, then pose
it.** That is exactly the teacher/scaffolding pattern from `emotion-depth.md` §2.4, with a
skeleton instead of a concept LoRA.

Two specifics:

- **CN applies to the body-framing half of the dataset only.** `_dataset_variation`
  already alternates face framings with body framings (`main.py:1166`). The close-ups exist
  to teach identity and should stay CN-free; the body shots get a skeleton. So `mode`
  gains a CN spread rather than every image being posed.
- **This is the external signal `emotion-depth.md` §2.6 asks for.** ControlNet-posed shots
  are named there as the mitigation that stops LoRA v2 from being trained on v1's own
  habits. Building H3c means enrichment (H1c) inherits it for free.

---

## 7. Open decisions — need the user before building

1. ~~**Which ControlNet?**~~ **RESOLVED 2026-07-28** — both installed and validated (§2);
   the `base_model` tag picks, NoobAI-native is the default for NoobAI checkpoints.
2. ~~**Framing.**~~ **RESOLVED 2026-07-28 (user): full body.** Starter set in §3.5; the
   consequence to watch is face fidelity (§5), not crop mismatch.
2b. ~~**Face pass.**~~ **RESOLVED 2026-07-28 (user): yes — build it.** Body from
   ControlNet, facial expression from a second pass over the top, each tunable per pose
   (§4.1). Denoise defaults to 0.45 and becomes a per-pose dial rather than a decision;
   tune it against the first real set. Portrait aspect (832×1216) alongside.
3. **Selection semantics.** Recommendation as written in §3.3: pick once, record it,
   re-roll explicitly. The alternative (random on every "Generate all") is simpler but
   makes a single-sprite regenerate non-reproducible.
4. **Bindings scope.** Ship global defaults + per-persona override (recommended), or
   per-persona only?
5. **Version numbering.** Recommendation: **stay in 0.8.x as Phase H stage H3**, since
   this is the same "make the sprite set actually expressive" arc that H1 belongs to and
   `emotion-depth.md` already depends on it. A new phase would take 0.9.x and push
   Character Studio again.
