# Phase 9 prompt language — what was actually measured

> Proof-of-principle run, 2026-07-28, before any code was written into the app.
> Prototype lived outside the repo; nothing in `backend/`, `frontend/` or on UR1 was
> touched. Companion to `prompt-language.md`.

Reference prompt for every test: the user's `copaxRealisticXLSDXL1_v32` glamour prompt
(27 positive phrases + 29 negative + render trailer = **56 phrases**).

---

## 1. Parser / compiler — deterministic, no LLM

| Claim | Result |
|---|---|
| Import loses no phrase | **56/56** round-tripped, phrase-for-phrase |
| Weights survive the round-trip | yes, incl. `(x:1.2)`, `(x)`→1.1, `[x]`→0.9 |
| Render trailer extracted | steps 40, cfg 8.0, sampler, model, 960×1280 |
| Slot classification without an LLM | **54/56 = 96%** (lexicon + heuristics) |
| Literal parens escaped | `a room (with an alcove)` → `(a room \(with an alcove\):1.2)` |

Only `Pretty Europe girl` and `Gently beautiful posture` fell through to `misc` — both
genuinely ambiguous. The 96% matters because it means the Ollama fallback in the importer
is an exception path, not the main path.

**Full A1111 syntax is covered** (`test_syntax.py`): multiplicative nesting
`((k))`=1.21 / `[[k]]`=0.81, `[from:to:factor]` scheduling, `BREAK`, and the 75-token
chunk budget. Two traps found by testing rather than reading:

- `[x]` (de-emphasis) and `[a:b:f]` (scheduling) share the bracket — only the
  colon-separated factor tells them apart.
- **Schedules are usually inline**, not phrase-level (`holding an [apple: fire: 0.9]`).
  Structuring those would mangle them; they are kept opaque inside the segment text and
  round-trip verbatim.

**Incidental find:** the reference prompt contains `bad hands` **twice** — once plain,
once as `(bad hands)` = ×1.1. A text blob hides that; a segment list surfaces it on import.

---

## 2. The edit script vs. the current whole-text rewrite

Seven realistic edits ("move her outside to a rainy Paris street at night", "ease off the
quality spam", "make this anime instead of a photograph", …), run against the same
reference prompt, on every installed model. Ollama 0.32.4 **JSON-schema-constrained
decoding** for both approaches.

| model | valid JSON | edit correct | **ops** kept (avg / worst) | **whole-text** kept (avg / worst) | s/edit |
|---|---|---|---|---|---|
| **gemma3:12b** | 7/7 | **6/7** | 97.2% / **92.9%** | 98.5% / 96.4% | **4.8s** |
| mistral:7b | 7/7 | **6/7** | 95.7% / 89.3% | 89.3% / 50.0% | 6.5s |
| deepseek-r1:8b | 7/7 | 4/7 | 96.4% / 94.6% | 91.3% / 73.2% | 6.4s |
| llama3.1:8b | 7/7 | 5/7 | 93.9% / 78.6% | 80.4% / **3.6%** | 8.3s |
| phi3:3.8b | 7/7 | 4/7 | **98.0% / 96.4%** | 33.4% / **0.0%** | 10.3s |

`kept` = % of the original 56 phrases still present after the edit.
Across **all** models and all runs: **0 collateral edits**, **0 lock violations**,
**0 expression leaks into `character`**.

### What this actually shows

**Schema-constrained decoding solves JSON validity outright.** 7/7 on every model
including a 3.8B one. Risk #1 in the design doc ("small-model JSON reliability") is
retired — no repair retry needed, though keep one for safety.

**The worst case is the metric, not the average.** Whole-text averages look fine
(80–98%) and hide single-turn collapses: llama3.1 kept **3.6%** on "make this an anime
illustration" — it discarded 54 of 56 phrases in one turn. phi3 zeroed the prompt
entirely on three separate instructions. The edit script never dropped below **78.6%**
on any model, and that floor is structural: an op that names no segment cannot delete one.

**The real win is model-independence, not raw accuracy.** With gemma3 the two approaches
are near-parity on preservation (97.2% vs 98.5%) — a good model *can* retype the prompt
verbatim. The edit script's advantage is that it holds at 93–98% regardless of model,
so a small fast model becomes viable. Whole-text forces a large model and is still not
safe on an 8B one.

**With a good model the case for ops rests on product, not reliability:** one op = one
accept/reject chip, server-enforced locks, and a self-writing version note. Those don't
come from a text diff at any model size.

### Corrections to earlier assumptions

- **"Avoid reasoning models" holds here.** `deepseek-r1` scored 4/7 on getting the edit
  *right* despite excellent preservation — it spends tokens deliberating and then emits a
  timid script. Not worth its latency for this job.
- **`mistral` was misjudged at first** (3/7) — that was a validator bug, not the model.
  After the fix it ties gemma3 at 6/7.
- **Bigger is not better on this task.** phi3 (3.8B) preserved best of all (98.0%) but was
  weakest semantically (4/7). Preservation is the *structure's* job; correctness is the
  model's. Don't read a high `kept` as quality — mistral scored 100% in an early run
  purely by doing almost nothing.

---

## 3. The validator does the real work

Every model produced malformed-but-recoverable output. All six needed the same
accommodations, so these are **requirements, not polish**:

| Model behaviour | Frequency | Accommodation |
|---|---|---|
| ids copied as `"[sty.set.1]"` from the view | every model | strip brackets before lookup |
| slot names invented: `chr.hair`, `pose/expression`, `style.camera` | every model | `normalise_slot()` — recover slot from any form |
| slot name prefixed into the text: `"setting: Rainy Paris street"` | gemma3, llama3 | `strip_slot_prefix()` |
| no-op replace (`'octane render' → 'octane render'`) | deepseek, gemma3 | drop silently |
| render values as strings (`"50"`) | every model | coerce to int/float |

Before these fixes the ranking was **wrong** — mistral looked like the worst model (3/7)
when it was mostly the harness rejecting well-formed intent over syntax. Budget real time
for the validator; it is the component that decides how good the models look.

**One rejection was the model being right and the prototype being wrong:** asked to make
her "smiling warmly", gemma3 correctly targeted `pose/expression` — which didn't exist as
a destination yet. The pose layer must be a first-class field in the op target space, or
the model gets punished for obeying the identity/expression rule.

---

## 4. Recommendation

**`gemma3:12b` for the edit script.** Best correctness (6/7), best worst-case behaviour,
and the *fastest* of the lot at 4.8s/edit — it beat llama3.1 on every axis simultaneously.
It is also the only installed model where the current whole-text approach is genuinely
safe, which makes it the low-risk choice whichever design ships.

Keep `llama3.1` as the fallback. Don't use `deepseek-r1` here. `phi3` is the "it still
works on a potato" datapoint, not a recommendation.

Untested, and worth pulling later: **qwen3:14b** (widely reported strongest at structured
output) and **mistral-small 3.2 24B** for the novel-ingest stage, which is a different
job — long-context comprehension rather than schema adherence — and can use a different
model, since the app already selects per call.
