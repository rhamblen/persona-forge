"""Test 3 — does a LOCAL model reliably emit a valid edit script, and which model?

Two things at once:
  A. Does the edit-script principle hold?  (vs. the current whole-text rewrite)
  B. Which of the installed Ollama models is best at it?

Run:  python bakeoff.py [model ...]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

from pf_prompt import (parse_prompt, compile_all, segment_view, apply_ops,
                       split_top_level, strip_weight, Segment)

OLLAMA = "http://192.168.1.32:11434"
KEEP_ALIVE = "5m"          # short: don't pin the user's VRAM after the test

REFERENCE = """1girl, Underwear, bikini, lace, Pretty Europe girl, (European interior scene:1.2), Dark room, The art of contrast photography, Gently beautiful posture, large breast, Beautiful hips, Well-proportioned figure, Fine facial details, A cinematic shot, (Low light shooting:1.3), octane render, Soft ambient light, Exquisite facial features, F/2.4, close-up, beautiful studio soft light, vibrant details, hyperrealistic, elegant, beautiful background, 8k, best quality
Negative prompt: bad anatomy, bad hands, multiple eyebrow, (cropped), extra limb, missing limbs, deformed hands, Long neck, two heads, bad breasts, bad butt, long body, (bad hands), signature, username, artist name, conjoined fingers, deformed fingers, ugly eyes, imperfect eyes, skewed eyes, unnatural face, unnatural body, error, painting by bad-artist, (worst quality:1.5), (low quality:1.5), (normal quality:1.5), lowres
Steps: 40, CFG scale: 8, Sampler: dpmpp_2m_sde_gpu, Model: copaxRealisticXLSDXL1_v32, width: 960, height: 1280"""

OPS_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string",
                           "enum": ["add", "replace", "remove", "set_weight",
                                    "disable", "enable", "render", "note"]},
                    "id": {"type": "string"},
                    "slot": {"type": "string"},
                    "text": {"type": "string"},
                    "weight": {"type": "number"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["op"],
            },
        }
    },
    "required": ["ops"],
}

OPS_SYSTEM = """You edit an image-generation prompt that is stored as a list of SEGMENTS.
You do NOT rewrite the prompt. You return a small list of EDIT OPERATIONS.

Each segment has an id like [sty.set.1], a slot, text, and an optional weight.

Available ops:
  {"op":"replace","id":"<id>","text":"<new text>"}   change a segment's text
  {"op":"add","slot":"<slot>","text":"<text>","weight":1.0}   add a new segment
  {"op":"remove","id":"<id>"}                        delete a segment
  {"op":"set_weight","id":"<id>","weight":1.2}       emphasis; 0.5-1.6, 1.0 = neutral
  {"op":"disable","id":"<id>"}                       turn a segment off, keep it
  {"op":"render","key":"steps","value":"50"}         change a render setting
  {"op":"note","text":"<observation>"}               a remark, changes nothing

RULES:
  - Emit ops ONLY for what the instruction asks. Never touch unrelated segments.
  - Segments marked [LOCKED] must not be edited. Skip them.
  - NEVER put an expression, emotion or mood in a `character` slot. Those belong
    in the pose/expression layer, not in the character's fixed identity.
  - Prefer the fewest ops that satisfy the instruction. Max 12.
  - Reply with ONLY the JSON object {"ops":[...]}."""

# The current (0.3.4) whole-text approach, as the control.
TEXT_SYSTEM = """You are a prompt-authoring assistant for an image generator.
You maintain THREE prompt fields: character, style, negative.
Make the SMALLEST possible edit that satisfies the instruction. This is a surgical
edit, not a rewrite.
  - Reproduce the current text VERBATIM — word for word, in the same order — and
    change ONLY the specific words the instruction is about.
  - Do NOT rephrase, reorder, summarise, shorten, 'improve', or drop any other
    wording. If a detail is unrelated to the instruction, it MUST appear unchanged.
  - Return the full text of all three fields, not just the diff.
Reply with ONLY: {"character": "...", "style": "...", "negative": "..."}"""

TEXT_SCHEMA = {
    "type": "object",
    "properties": {"character": {"type": "string"}, "style": {"type": "string"},
                   "negative": {"type": "string"}},
    "required": ["character", "style", "negative"],
}

# (instruction, what a correct answer must do)
CASES = [
    ("Move her outside to a rainy Paris street at night.",
     {"touch_slots": {"setting"}, "must_not_touch": {"outfit", "body", "face"}}),
    ("Push the European interior emphasis down a bit and make the low-light less strong.",
     {"want_ops": {"set_weight"}, "must_not_touch": {"outfit", "body"}}),
    ("Give her long red hair and green eyes.",
     {"want_ops": {"add"}, "touch_slots": {"hair", "eyes"}}),
    ("Drop the octane render, and push steps up to 50.",
     {"want_ops": {"remove", "render", "disable"}}),
    ("She should be smiling warmly.",
     {"trap": "expression_into_character"}),
    ("The quality spam in the negative is washing the image out — ease it off.",
     {"touch_slots": {"quality_floor"}, "must_not_touch": {"outfit", "hair"}}),
    ("Make this an anime illustration instead of a photograph.",
     {"touch_slots": {"medium", "style_bleed", "art_direction", "camera", "quality"}}),
]


def ollama(model: str, system: str, prompt: str, schema=None, timeout=240):
    body = {"model": model, "system": system, "prompt": prompt, "stream": False,
            "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.2}}
    body["format"] = schema if schema else "json"
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", method="POST",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data.get("response", ""), time.time() - t0


def extract_json(text: str):
    text = (text or "").strip()
    # reasoning models (deepseek-r1) wrap output in <think>…</think>
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    return None


def phrase_set(s: str) -> set[str]:
    out = set()
    for p in split_top_level(s or ""):
        body, w = strip_weight(p)
        out.add(f"{body.strip().lower()}@{w:g}")
    return out


def run_ops(model: str, segs, render, instruction: str):
    view = segment_view(segs, render)
    prompt = f"CURRENT PROMPT\n{view}\n\nINSTRUCTION\n{instruction}\n\nReturn the edit script."
    raw, dt = ollama(model, OPS_SYSTEM, prompt, OPS_SCHEMA)
    data = extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
        return {"ok": False, "secs": dt, "raw": raw[:300]}
    new_segs, new_render, applied, rejected = apply_ops(segs, render, data["ops"])
    return {"ok": True, "secs": dt, "ops": data["ops"], "applied": applied,
            "rejected": rejected, "segs": new_segs, "render": new_render}


def run_text(model: str, fields: dict, instruction: str):
    prompt = (f"User instruction:\n{instruction}\n\nCurrent values:\n"
              f"  character: {fields['character']}\n"
              f"  style: {fields['style']}\n"
              f"  negative: {fields['negative']}\n")
    raw, dt = ollama(model, TEXT_SYSTEM, prompt, TEXT_SCHEMA)
    data = extract_json(raw)
    if not isinstance(data, dict):
        return {"ok": False, "secs": dt, "raw": raw[:300]}
    return {"ok": True, "secs": dt, "fields": data}


def main():
    models = sys.argv[1:] or ["llama3.1:latest"]
    base_segs, base_render = parse_prompt(REFERENCE)
    base_fields = compile_all(base_segs)
    # lock two segments to test enforcement
    for s in base_segs:
        if s.slot in ("subject",) or (s.slot == "outfit" and s.text.lower() == "bikini"):
            s.locked = True
    locked_ids = {s.id for s in base_segs if s.locked}
    orig_phrases = {f: phrase_set(v) for f, v in base_fields.items()}
    total_orig = sum(len(v) for v in orig_phrases.values())

    print(f"Baseline: {len(base_segs)} segments, {total_orig} distinct phrases, "
          f"{len(locked_ids)} locked\n")

    results = {}
    for model in models:
        print("=" * 78)
        print(f"MODEL: {model}")
        print("=" * 78)
        stat = {"json_ok": 0, "n": 0, "secs": 0.0, "ops_total": 0, "ops_rejected": 0,
                "preserved": [], "lock_violations": 0, "trap_fail": 0, "goal_hit": 0,
                "goal_n": 0, "collateral": 0}
        for instruction, expect in CASES:
            stat["n"] += 1
            try:
                r = run_ops(model, base_segs, base_render, instruction)
            except Exception as exc:
                print(f"  ERR  {instruction[:44]:<46} {type(exc).__name__}: {exc}")
                continue
            stat["secs"] += r["secs"]
            if not r["ok"]:
                print(f"  BAD JSON  {instruction[:44]:<42} {r['secs']:5.1f}s  {r.get('raw','')[:70]}")
                continue
            stat["json_ok"] += 1
            stat["ops_total"] += len(r["ops"])
            stat["ops_rejected"] += len(r["rejected"])
            stat["lock_violations"] += sum(1 for x in r["rejected"] if "LOCKED" in x)

            new_fields = compile_all(r["segs"])
            new_phrases = {f: phrase_set(v) for f, v in new_fields.items()}
            # how much of the ORIGINAL survived
            kept = sum(len(orig_phrases[f] & new_phrases[f]) for f in orig_phrases)
            stat["preserved"].append(kept / total_orig)

            touched = {s.slot for s in r["segs"] if s.origin == "ai"}
            removed_slots = {s.slot for s in base_segs
                             if s.id not in {x.id for x in r["segs"]}}
            touched |= removed_slots
            for x in r["applied"]:
                for sl in ("quality_floor", "medium", "setting", "hair", "eyes",
                           "style_bleed", "art_direction", "camera", "quality"):
                    if f"/{sl}:" in x or f" {sl}:" in x:
                        touched.add(sl)

            ok_goal = True
            if "touch_slots" in expect:
                stat["goal_n"] += 1
                ok_goal = bool(touched & expect["touch_slots"])
                stat["goal_hit"] += int(ok_goal)
            elif "want_ops" in expect:
                stat["goal_n"] += 1
                ok_goal = bool({o.get("op") for o in r["ops"]} & expect["want_ops"])
                stat["goal_hit"] += int(ok_goal)
            if "must_not_touch" in expect:
                bad = touched & expect["must_not_touch"]
                stat["collateral"] += len(bad)
            if expect.get("trap") == "expression_into_character":
                stat["goal_n"] += 1
                leaked = any(
                    o.get("op") in ("add", "replace")
                    and (o.get("slot", "") in
                         ("subject", "face", "age_build", "body", "outfit", "hair",
                          "eyes", "skin", "marks", "accessories"))
                    for o in r["ops"])
                stat["trap_fail"] += int(leaked)
                stat["goal_hit"] += int(not leaked)
                ok_goal = not leaked

            mark = "ok " if ok_goal else "MISS"
            print(f"  {mark} {instruction[:44]:<46} {r['secs']:5.1f}s "
                  f"{len(r['ops'])} ops, {len(r['rejected'])} rej, "
                  f"{100*stat['preserved'][-1]:.0f}% kept")
            for a in r["applied"][:4]:
                print(f"        + {a[:88]}")
            for x in r["rejected"][:3]:
                print(f"        - REJECTED {x[:80]}")
        # ---- whole-text control on THIS model, same cases ----
        cpres, cworst = [], 1.0
        for instruction, _ in CASES:
            try:
                r = run_text(model, base_fields, instruction)
            except Exception:
                continue
            if not r["ok"]:
                cpres.append(0.0); cworst = 0.0
                continue
            np_ = {f: phrase_set(r["fields"].get(f, "")) for f in orig_phrases}
            kept = sum(len(orig_phrases[f] & np_[f]) for f in orig_phrases) / total_orig
            cpres.append(kept); cworst = min(cworst, kept)
        stat["ctrl"] = 100 * sum(cpres) / len(cpres) if cpres else 0.0
        stat["ctrl_worst"] = 100 * cworst
        stat["ops_worst"] = 100 * min(stat["preserved"]) if stat["preserved"] else 0.0
        print(f"  whole-text control on {model}: {stat['ctrl']:.1f}% avg, "
              f"{stat['ctrl_worst']:.1f}% worst case")
        results[model] = stat
        print()

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"{'model':<20}{'JSON':>6}{'goal':>6}{'ops kept':>10}{'ops worst':>11}"
          f"{'text kept':>11}{'text worst':>12}{'collat':>8}{'s/edit':>8}")
    for m, s in results.items():
        pres = 100 * sum(s["preserved"]) / len(s["preserved"]) if s["preserved"] else 0
        goal = f"{s['goal_hit']}/{s['goal_n']}"
        print(f"{m:<20}{s['json_ok']}/{s['n']:<4}{goal:>6}{pres:9.1f}%"
              f"{s.get('ops_worst',0):10.1f}%{s.get('ctrl',0):10.1f}%"
              f"{s.get('ctrl_worst',0):11.1f}%{s['collateral']:8}"
              f"{s['secs']/max(1,s['n']):7.1f}s")
    print("\nkept  = % of the original 56 phrases still present after the edit")
    print("collat= edits to slots the instruction had no business touching")
    print("trap  = times an expression was leaked into the character identity")


if __name__ == "__main__":
    main()
