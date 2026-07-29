"""Test 1 — parser + compiler. No Ollama, no network. Pure round-trip fidelity."""

import sys
from pf_prompt import (parse_prompt, compile_all, split_top_level, strip_weight,
                       render_segment, Segment, classify, FIELD_SLOTS)

REFERENCE = """1girl, Underwear, bikini, lace, Pretty Europe girl, (European interior scene:1.2), Dark room, The art of contrast photography, Gently beautiful posture, large breast, Beautiful hips, Well-proportioned figure, Fine facial details, A cinematic shot, (Low light shooting:1.3), octane render, Soft ambient light, Exquisite facial features, F/2.4, close-up, beautiful studio soft light, vibrant details, hyperrealistic, elegant, beautiful background, 8k, best quality
Negative prompt: bad anatomy, bad hands, multiple eyebrow, (cropped), extra limb, missing limbs, deformed hands, Long neck, two heads, bad breasts, bad butt, long body, (bad hands), signature, username, artist name, conjoined fingers, deformed fingers, ugly eyes, imperfect eyes, skewed eyes, unnatural face, unnatural body, error, painting by bad-artist, (worst quality:1.5), (low quality:1.5), (normal quality:1.5), lowres
Steps: 40, CFG scale: 8, Sampler: dpmpp_2m_sde_gpu, Model: copaxRealisticXLSDXL1_v32, width: 960, height: 1280"""

fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


print("=" * 78)
print("TEST 1a — split + weight parsing primitives")
print("=" * 78)
check("splits on top-level commas only",
      split_top_level("a, (b:1.2), c") == ["a", "(b:1.2)", "c"])
check("comma inside parens is not a split point",
      split_top_level("(a, b:1.2), c") == ["(a, b:1.2)", "c"],
      str(split_top_level("(a, b:1.2), c")))
check("escaped paren survives split",
      split_top_level(r"foo \(bar\), baz") == [r"foo \(bar\)", "baz"],
      str(split_top_level(r"foo \(bar\), baz")))
check("(x:1.2) -> weight 1.2", strip_weight("(European interior scene:1.2)")
      == ("European interior scene", 1.2))
check("bare (x) -> weight 1.1 (A1111 rule)", strip_weight("(cropped)") == ("cropped", 1.1))
check("plain -> weight 1.0", strip_weight("octane render") == ("octane render", 1.0))

print()
print("=" * 78)
print("TEST 1b — import the reference prompt")
print("=" * 78)
segs, render = parse_prompt(REFERENCE)
raw_pos = split_top_level(REFERENCE.split("Negative prompt:")[0])
raw_neg = split_top_level(
    REFERENCE.split("Negative prompt:")[1].split("Steps:")[0])

print(f"  parsed {len(segs)} segments  ({len(raw_pos)} positive + {len(raw_neg)} negative"
      f" = {len(raw_pos) + len(raw_neg)} input phrases)")
check("no phrase lost in import", len(segs) == len(raw_pos) + len(raw_neg),
      f"got {len(segs)}")
check("render settings extracted", render.get("steps") == 40 and render.get("cfg") == 8.0
      and render.get("width") == 960 and render.get("height") == 1280,
      str(render))
check("sampler extracted", render.get("sampler") == "dpmpp_2m_sde_gpu", str(render))
check("model extracted", render.get("model") == "copaxRealisticXLSDXL1_v32", str(render))
print(f"  render = {render}")

print()
print("  --- classification ---")
unresolved = [s for s in segs if s.slot == "misc"]
by_slot: dict[str, list[str]] = {}
for s in segs:
    by_slot.setdefault(f"{s.field}/{s.slot}", []).append(
        s.text + (f" ({s.weight:g})" if s.weight != 1.0 else ""))
for k in sorted(by_slot):
    print(f"   {k:<22} {', '.join(by_slot[k])}")
rate = 100 * (len(segs) - len(unresolved)) / len(segs)
print(f"\n  classified {len(segs) - len(unresolved)}/{len(segs)} = {rate:.0f}% "
      f"deterministically (lexicon+heuristics, no LLM)")
print(f"  unresolved -> misc: {[s.text for s in unresolved]}")
check("classification rate >= 80%", rate >= 80, f"{rate:.0f}%")

print()
print("=" * 78)
print("TEST 1c — round-trip fidelity (the load-bearing claim)")
print("=" * 78)
out = compile_all(segs, order=False)          # order=False -> preserve input order
rebuilt_pos = ", ".join(x for x in [out["character"], out["style"]] if x)
orig_pos = ", ".join(t for t in raw_pos)
orig_neg = ", ".join(t for t in raw_neg)


def norm(s: str) -> str:
    """Compare as a multiset of (phrase, weight) — ordering across fields is by
    design, and a bare `(x)` is A1111 for `(x:1.1)`, so compare *semantically*
    rather than by literal text."""
    out = []
    for x in split_top_level(s):
        body, w = strip_weight(x)
        out.append(f"{body.strip().lower()}@{w:g}")
    return sorted(out)


check("positive round-trips phrase-for-phrase (incl. weights)",
      norm(rebuilt_pos) == norm(orig_pos),
      f"\n     lost: {set(norm(orig_pos)) - set(norm(rebuilt_pos))}"
      f"\n     new:  {set(norm(rebuilt_pos)) - set(norm(orig_pos))}")
check("negative round-trips phrase-for-phrase (incl. weights)",
      norm(out["negative"]) == norm(orig_neg),
      f"\n     lost: {set(norm(orig_neg)) - set(norm(out['negative']))}"
      f"\n     new:  {set(norm(out['negative'])) - set(norm(orig_neg))}")

print()
ordered = compile_all(segs, order=True)
print("  compiled CHARACTER:\n   ", ordered["character"])
print("  compiled STYLE:\n   ", ordered["style"])
print("  compiled NEGATIVE:\n   ", ordered["negative"][:200], "...")

print()
print("=" * 78)
print("TEST 1d — paren escaping (risk #2 in the design doc)")
print("=" * 78)
nasty = Segment("x", "style", "setting", "a room (with an alcove)", 1.2)
r = render_segment(nasty)
print(f"  segment text : {nasty.text!r} @ {nasty.weight}")
print(f"  compiled     : {r}")
check("literal parens escaped", r == r"(a room \(with an alcove\):1.2)", r)
check("re-split of a compiled nasty segment stays one phrase",
      len(split_top_level(r + ", next phrase")) == 2,
      str(split_top_level(r + ", next phrase")))

roundtrip_body, roundtrip_w = strip_weight(r)
check("weight survives the escape round-trip", roundtrip_w == 1.2, str(roundtrip_w))

print()
print("=" * 78)
print(f"{'ALL DETERMINISTIC TESTS PASSED' if not fails else f'{fails} FAILURE(S)'}")
print("=" * 78)
sys.exit(1 if fails else 0)
