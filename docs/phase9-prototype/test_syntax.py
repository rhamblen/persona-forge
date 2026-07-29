"""Test 2 — full A1111 syntax per stable-diffusion-art.com/prompt-guide/.

Covers the mechanics the guide documents that my first pass missed:
  [x] de-emphasis, multiplicative nesting, [from:to:factor] scheduling, BREAK,
  and the 75-token CLIP chunk budget.
"""

import sys
from pf_prompt import (strip_weight, parse_schedule, split_top_level, parse_field,
                       find_inline_schedules,
                       compile_field, render_segment, estimate_tokens, chunk_report,
                       Segment, BREAK)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


print("=" * 78)
print("TEST 2a — emphasis syntax, incl. the guide's multiplicative nesting table")
print("=" * 78)
cases = [
    ("(keyword)",       "keyword", 1.1),
    ("((keyword))",     "keyword", 1.21),
    ("(((keyword)))",   "keyword", 1.33),
    ("[keyword]",       "keyword", 0.9),
    ("[[keyword]]",     "keyword", 0.81),
    ("[[[keyword]]]",   "keyword", 0.73),
    ("(dog: 0.5)",      "dog",     0.5),
    ("(dog: 1.5)",      "dog",     1.5),
    ("plain",           "plain",   1.0),
]
for raw, want_text, want_w in cases:
    got_text, got_w = strip_weight(raw)
    check(f"{raw:<16} -> ({want_text!r}, {want_w})",
          got_text == want_text and abs(got_w - want_w) < 0.005,
          f"got ({got_text!r}, {got_w})")

print()
print("=" * 78)
print("TEST 2b — keyword blending / prompt scheduling  [from:to:factor]")
print("=" * 78)
check("[Joe Biden: Donald Trump: 0.5] parses",
      parse_schedule("[Joe Biden: Donald Trump: 0.5]") == ("Joe Biden", "Donald Trump", 0.5),
      str(parse_schedule("[Joe Biden: Donald Trump: 0.5]")))
check("[apple: fire: 0.9] parses",
      parse_schedule("[apple: fire: 0.9]") == ("apple", "fire", 0.9))
check("a plain [keyword] is NOT read as a schedule",
      parse_schedule("[keyword]") is None)
check("...and still de-emphasises to 0.9", strip_weight("[keyword]") == ("keyword", 0.9))
print("   ^ this is the collision the guide implies: [x] and [a:b:f] share a bracket.")

# Phrase-level: the whole phrase IS the schedule -> structured, editable.
plevel = parse_field("portrait, [Emma Watson: Amber Heard: 0.85], sharp focus", "style")
sch = [s for s in plevel if s.schedule]
check("phrase-level schedule is captured structurally", len(sch) == 1, str(len(sch)))
out_p = compile_field(plevel, "style", order=False)
check("phrase-level schedule recompiles", "[Emma Watson:Amber Heard:0.85]" in out_p, out_p)

# Inline: the guide's own examples. Stays inside the segment text, round-trips verbatim.
sched_txt = "photo of a woman, holding an [apple: fire: 0.9], sharp focus"
segs = parse_field(sched_txt, "style")
out = compile_field(segs, "style", order=False)
inline = [s for s in segs if find_inline_schedules(s.text)]
check("inline schedule is DETECTED (so the UI can badge it)", len(inline) == 1,
      str(len(inline)))
check("inline schedule round-trips byte-identically as text", out == sched_txt,
      f"\n     in : {sched_txt}\n     out: {out}")
print(f"   in  : {sched_txt}")
print(f"   out : {out}")
print(f"   detected inline: {find_inline_schedules(inline[0].text) if inline else '—'}")
print("   ^ inline schedules are opaque-but-preserved; only whole-phrase ones get")
print("     structured editing. Both survive the round-trip, which is what matters.")

print()
print("=" * 78)
print("TEST 2c — BREAK (the attribute-bleed fix from the guide)")
print("=" * 78)
brk = parse_field("photo of a woman in white hat, BREAK, blue dress", "style")
check("BREAK becomes a segment", any(s.text == BREAK for s in brk))
out = compile_field(brk, "style", order=False)
check("BREAK re-emits as the bare keyword", "BREAK" in out, out)
print(f"   out : {out}")
print("   ^ segments make BREAK placement a first-class, draggable thing rather")
print("     than punctuation the user has to remember.")

print()
print("=" * 78)
print("TEST 2d — 75-token chunk budget / dilution warning")
print("=" * 78)
REFERENCE_POS = ("1girl, Underwear, bikini, lace, Pretty Europe girl, "
                 "(European interior scene:1.2), Dark room, The art of contrast photography, "
                 "Gently beautiful posture, large breast, Beautiful hips, "
                 "Well-proportioned figure, Fine facial details, A cinematic shot, "
                 "(Low light shooting:1.3), octane render, Soft ambient light, "
                 "Exquisite facial features, F/2.4, close-up, beautiful studio soft light, "
                 "vibrant details, hyperrealistic, elegant, beautiful background, 8k, best quality")
rep = chunk_report(REFERENCE_POS)
print(f"   reference positive : ~{rep['tokens']} tokens -> {rep['chunks']} CLIP chunks")
print(f"   phrase straddling a 75-token boundary: {rep['straddling']}")
check("token estimate is in a sane range for this prompt",
      50 <= rep["tokens"] <= 130, str(rep["tokens"]))
check("reference prompt spills past one 75-token chunk", rep["chunks"] >= 2,
      f"{rep['chunks']} chunk(s)")
print("   ^ the guide: a token at the START of a chunk is more effective, and")
print("     chunks are encoded independently. So slot ORDER is load-bearing, and")
print("     a straddling phrase gets silently split across two encodings.")

print()
print("=" * 78)
print(f"{'ALL SYNTAX TESTS PASSED' if not fails else f'{fails} FAILURE(S)'}")
print("=" * 78)
sys.exit(1 if fails else 0)
