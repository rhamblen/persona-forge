"""Phase-9 prompt-language prototype — parser, compiler, edit-script engine.

Standalone proof of principle for docs/prompt-language.md. Touches nothing in the
repo and nothing on UR1. Everything here is deterministic except apply_ops' input,
which is what the Ollama half of the test supplies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------------------- #
# Slots
# --------------------------------------------------------------------------- #

CHARACTER_SLOTS = ["subject", "age_build", "face", "hair", "eyes", "skin",
                   "body", "outfit", "accessories", "marks", "misc"]
STYLE_SLOTS = ["medium", "art_direction", "artist_ref", "setting", "time_light",
               "camera", "composition", "palette", "details", "quality", "misc"]
NEGATIVE_SLOTS = ["anatomy", "face_defects", "artifacts", "quality_floor",
                  "style_bleed", "content", "misc"]
# The pose/expression layer — a real field, kept strictly separate from `character`
# so an expression can never contaminate identity. Models correctly try to target
# it ("she should be smiling warmly"), so it must exist as a destination.
POSE_SLOTS = ["pose", "expression", "gaze", "gesture", "misc"]

FIELD_SLOTS = {"character": CHARACTER_SLOTS, "style": STYLE_SLOTS,
               "negative": NEGATIVE_SLOTS, "pose": POSE_SLOTS}
SLOT_FIELD = {}
for _f, _ss in FIELD_SLOTS.items():
    for _s in _ss:
        if _s != "misc":
            SLOT_FIELD.setdefault(_s, _f)

SLOT_ABBR = {  # for readable ids
    "subject": "sub", "age_build": "age", "face": "fac", "hair": "hai",
    "eyes": "eye", "skin": "skn", "body": "bod", "outfit": "out",
    "accessories": "acc", "marks": "mrk", "medium": "med",
    "art_direction": "art", "setting": "set", "time_light": "lgt",
    "camera": "cam", "composition": "cmp", "palette": "pal", "quality": "qua",
    "anatomy": "ana", "face_defects": "fdf", "artifacts": "atf",
    "quality_floor": "qfl", "style_bleed": "bld", "content": "cnt",
    "artist_ref": "ref", "details": "det", "misc": "msc",
    "pose": "pos", "expression": "exp", "gaze": "gaz", "gesture": "ges",
}
FIELD_ABBR = {"character": "chr", "style": "sty", "negative": "neg", "pose": "pse"}


def normalise_slot(raw: Any) -> str:
    """Models write slots as 'chr.hair', 'pose/expression', 'Setting', 'style.camera'.

    Measured across six models: every one of them invented at least one of these
    forms. Recover the slot rather than rejecting the op.
    """
    s = str(raw or "").strip().lower().replace(" ", "_")
    for sep in ("/", ".", ":"):
        if sep in s:
            head, _, tail = s.rpartition(sep)
            if tail in SLOT_ABBR or tail in FIELD_SLOTS:
                s = tail
            elif head in FIELD_SLOTS or head in FIELD_ABBR.values():
                s = tail
    if s in SLOT_ABBR:
        return s
    for name, ab in SLOT_ABBR.items():        # given the abbreviation
        if s == ab:
            return name
    return s


_SLOT_PREFIX_RE = re.compile(
    r"^\s*([a-z_]{3,14})\s*:\s+(?=\S)", re.I)


def strip_slot_prefix(text: str, slot: str = "") -> str:
    """`replace` text often comes back as 'setting: Rainy Paris street' because the
    view renders segments as `slot: text`. Strip a leading slot-name prefix."""
    m = _SLOT_PREFIX_RE.match(text or "")
    if not m:
        return text
    head = normalise_slot(m.group(1))
    if head in SLOT_ABBR or head == normalise_slot(slot):
        return text[m.end():].strip()
    return text

# --------------------------------------------------------------------------- #
# Lexicon — phrase (lowercased) -> slot. The deterministic 80%.
# --------------------------------------------------------------------------- #

LEXICON: dict[str, str] = {}


def _lex(slot: str, *phrases: str) -> None:
    for p in phrases:
        LEXICON[p.lower()] = slot


_lex("subject", "1girl", "1boy", "solo", "2girls", "one woman", "one man")
_lex("age_build", "well-proportioned figure", "slim", "athletic build", "petite",
     "curvy", "young adult", "mature woman", "teenager", "tall", "muscular")
_lex("face", "fine facial details", "exquisite facial features", "high cheekbones",
     "soft jawline", "detailed face", "beautiful face", "sharp features")
_lex("hair", "long hair", "short hair", "blonde hair", "black hair", "red hair",
     "brown hair", "silver hair", "ponytail", "braided hair", "bob cut", "wavy hair")
_lex("eyes", "blue eyes", "green eyes", "brown eyes", "hazel eyes", "grey eyes",
     "amber eyes", "detailed eyes", "heterochromia")
_lex("skin", "pale skin", "tanned skin", "freckled skin", "dark skin", "olive skin",
     "smooth skin", "realistic skin texture")
_lex("body", "large breast", "large breasts", "small breasts", "beautiful hips",
     "wide hips", "narrow waist", "long legs", "toned stomach")
_lex("outfit", "underwear", "bikini", "lace", "lingerie", "evening gown", "dress",
     "school uniform", "business suit", "leather jacket", "hoodie", "armour",
     "armor", "kimono", "swimsuit", "jeans", "blouse", "coat", "robe")
_lex("accessories", "earrings", "necklace", "glasses", "sunglasses", "hat",
     "scarf", "gloves", "watch", "choker", "hair ribbon")
_lex("marks", "freckles", "scar", "tattoo", "beauty mark", "mole under eye")

_lex("medium", "photograph", "photo", "anime illustration", "anime screencap",
     "comic panel", "oil painting", "watercolour", "watercolor", "digital painting",
     "3d render", "manga panel", "concept art")
_lex("art_direction", "the art of contrast photography", "octane render",
     "a cinematic shot", "cinematic shot", "unreal engine", "film still",
     "studio photography", "editorial photography", "cel shading", "clean lineart",
     "halftone shading", "bold ink outlines")
_lex("setting", "european interior scene", "european interior", "dark room",
     "beautiful background", "outdoors", "city street", "forest", "bedroom",
     "cafe", "beach", "rooftop", "library", "throne room", "laboratory")
_lex("time_light", "low light shooting", "soft ambient light", "golden hour",
     "backlit", "rim lighting", "harsh sunlight", "candlelight", "moonlight",
     "neon lighting", "overcast", "night", "sunset", "volumetric lighting")
_lex("camera", "f/2.4", "f/1.8", "f/4", "close-up", "closeup", "medium shot",
     "wide shot", "full body", "upper body", "portrait", "from above", "from below",
     "beautiful studio soft light", "shallow depth of field", "bokeh", "35mm", "85mm",
     "depth of field")
_lex("composition", "rule of thirds", "centred composition", "centered composition",
     "symmetrical", "dynamic angle", "dutch angle", "negative space")
_lex("palette", "vibrant details", "vibrant colors", "vibrant colours",
     "muted palette", "monochrome", "warm tones", "cool tones", "high contrast",
     "flat colors", "flat colours", "desaturated")
_lex("artist_ref", "artstation", "deviantart", "trending on artstation",
     "cgsociety", "pixiv", "by greg rutkowski", "by alphonse mucha",
     "by john singer sargent", "unreal engine 5")
_lex("details", "sci-fi", "dystopian", "fantasy", "dark art", "steampunk",
     "cyberpunk", "post-apocalyptic", "ethereal", "surreal", "gothic", "baroque")
_lex("quality", "hyperrealistic", "photorealistic", "elegant", "8k", "4k",
     "best quality", "masterpiece", "highly detailed", "sharp focus",
     "intricate details", "high resolution", "ultra detailed")

_lex("anatomy", "bad anatomy", "bad hands", "deformed hands", "extra limb",
     "extra limbs", "missing limbs", "long neck", "two heads", "long body",
     "bad breasts", "bad butt", "conjoined fingers", "deformed fingers",
     "extra fingers", "fused fingers", "mutated hands", "unnatural body",
     "bad proportions", "extra arms", "extra legs", "malformed limbs")
_lex("face_defects", "ugly eyes", "imperfect eyes", "skewed eyes", "multiple eyebrow",
     "multiple eyebrows", "unnatural face", "asymmetric eyes", "cross-eyed",
     "bad teeth", "deformed face")
_lex("artifacts", "signature", "username", "artist name", "cropped", "error",
     "painting by bad-artist", "watermark", "text", "jpeg artifacts", "logo",
     "border", "frame", "out of frame", "duplicate")
_lex("quality_floor", "worst quality", "low quality", "normal quality", "lowres",
     "blurry", "grainy", "pixelated")
_lex("style_bleed", "anime", "cartoon", "illustration", "3d", "cgi", "sketch",
     "realistic", "photorealism", "cel shaded")

# Heuristic patterns applied when the lexicon misses.
HEURISTICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^f/\d", re.I), "camera"),
    (re.compile(r"\b\d{2,3}mm\b", re.I), "camera"),
    (re.compile(r"\b(shot|angle|framing|lens|focal|view)\b", re.I), "camera"),
    (re.compile(r"\b(light|lighting|lit|shadow|glow|dusk|dawn|night|sunlit)\b", re.I), "time_light"),
    (re.compile(r"\b(hair|bangs|fringe|braid)\b", re.I), "hair"),
    (re.compile(r"\b(eyes?|iris|pupils?)\b", re.I), "eyes"),
    (re.compile(r"\b(skin|complexion)\b", re.I), "skin"),
    (re.compile(r"\b(wearing|dressed|outfit|clothes|clothing|garment)\b", re.I), "outfit"),
    (re.compile(r"\b(quality|detailed|resolution|masterpiece|\dk)\b", re.I), "quality"),
    (re.compile(r"\b(bad|deformed|malformed|mutated|missing|extra|ugly)\b", re.I), "anatomy"),
    (re.compile(r"\b(interior|exterior|room|street|background|scene|landscape)\b", re.I), "setting"),
]


# --------------------------------------------------------------------------- #
# Segment
# --------------------------------------------------------------------------- #

@dataclass
class Segment:
    id: str
    field: str          # character | style | negative
    slot: str
    text: str
    weight: float = 1.0
    enabled: bool = True
    locked: bool = False
    origin: str = "user"
    # keyword blending / prompt scheduling: [from, to, factor] -> `[from:to:factor]`
    schedule: list | None = None


def _new_id(field: str, slot: str, existing: set[str]) -> str:
    base = f"{FIELD_ABBR[field]}.{SLOT_ABBR.get(slot, 'msc')}"
    n = 1
    while f"{base}.{n}" in existing:
        n += 1
    sid = f"{base}.{n}"
    existing.add(sid)
    return sid


# --------------------------------------------------------------------------- #
# Parser:  A1111-style string -> [Segment]
# --------------------------------------------------------------------------- #

_WEIGHT_RE = re.compile(r"^\((.*):\s*([0-9]*\.?[0-9]+)\)$", re.DOTALL)
_BARE_PAREN_RE = re.compile(r"^\((.*)\)$", re.DOTALL)
_BARE_BRACKET_RE = re.compile(r"^\[(.*)\]$", re.DOTALL)
# prompt scheduling / keyword blending:  [from:to:factor]  |  [to:factor]  |  [from::factor]
_SCHEDULE_RE = re.compile(r"^\[(.*?):(.*?):\s*([0-9]*\.?[0-9]+)\]$", re.DOTALL)

BREAK = "__BREAK__"        # sentinel segment text -> emits the A1111 BREAK keyword


def _balanced(text: str, open_ch: str, close_ch: str) -> bool:
    """True if text is wrapped in ONE matching unescaped pair spanning the whole string."""
    if len(text) < 2 or text[0] != open_ch or text[-1] != close_ch:
        return False
    depth, i = 0, 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False        # closed early -> not a single wrapper
        i += 1
    return depth == 0


def split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside (unescaped) parentheses."""
    parts, buf, depth, i = [], [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_schedule(phrase: str) -> tuple[str, str, float] | None:
    """`[from:to:factor]` keyword blending / prompt scheduling (guide §Keyword blending).

    Must be tried BEFORE bare-bracket de-emphasis: `[a:b:0.5]` and `[a]` share the
    bracket. Disambiguated by the presence of the colon-separated factor.
    """
    phrase = phrase.strip()
    if not _balanced(phrase, "[", "]"):
        return None
    m = _SCHEDULE_RE.match(phrase)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip(), round(float(m.group(3)), 3)


_INLINE_SCHEDULE_RE = re.compile(r"\[([^\[\]:]*?):([^\[\]:]*?):\s*([0-9]*\.?[0-9]+)\]")


def find_inline_schedules(text: str) -> list[tuple[str, str, float]]:
    """Schedules embedded *inside* a phrase, e.g. `holding an [apple: fire: 0.9]`.

    The guide's own examples are inline, so this is the common case — not the
    phrase-level one. Inline schedules stay part of the segment's text (they
    round-trip verbatim); this just makes them visible so the UI can badge the
    segment and the AI can be told not to mangle them.
    """
    return [(m.group(1).strip(), m.group(2).strip(), round(float(m.group(3)), 3))
            for m in _INLINE_SCHEDULE_RE.finditer(text)]


def strip_weight(phrase: str) -> tuple[str, float]:
    """Unwrap A1111 emphasis to (text, weight), honouring the multiplicative nesting
    documented in the guide:
        (x:1.2) -> 1.2      (x) -> 1.1      ((x)) -> 1.21      (((x))) -> 1.33
        [x]     -> 0.9      [[x]] -> 0.81   [[[x]]] -> 0.73    x -> 1.0
    """
    phrase = phrase.strip()
    weight = 1.0
    while True:
        m = _WEIGHT_RE.match(phrase)
        if m and _balanced(phrase, "(", ")"):
            # explicit factor terminates the walk (A1111 does not nest past it)
            return m.group(1).strip(), round(weight * float(m.group(2)), 2)
        if _balanced(phrase, "(", ")"):
            inner = _BARE_PAREN_RE.match(phrase).group(1)
            weight = round(weight * 1.1, 4)
            phrase = inner.strip()
            continue
        if _balanced(phrase, "[", "]") and not _SCHEDULE_RE.match(phrase):
            inner = _BARE_BRACKET_RE.match(phrase).group(1)
            weight = round(weight * 0.9, 4)
            phrase = inner.strip()
            continue
        return phrase, round(weight, 2)


def classify(phrase: str, field: str) -> tuple[str, str]:
    """Return (slot, how) where how ∈ lexicon|heuristic|unresolved."""
    allowed = FIELD_SLOTS[field]
    low = phrase.lower().strip()
    slot = LEXICON.get(low)
    if slot and slot in allowed:
        return slot, "lexicon"
    # try the lexicon on comma-free sub-phrases (e.g. "close-up, F/2.4" merged)
    for pat, slot in HEURISTICS:
        if slot in allowed and pat.search(phrase):
            return slot, "heuristic"
    return "misc", "unresolved"


def parse_field(text: str, field: str, existing: set[str] | None = None,
                origin: str = "user") -> list[Segment]:
    existing = existing if existing is not None else set()
    out: list[Segment] = []
    for raw in split_top_level(text):
        if raw.strip() == "BREAK":
            out.append(Segment(_new_id(field, "misc", existing), field, "misc",
                               BREAK, origin=origin))
            continue
        sched = parse_schedule(raw)
        if sched:
            a, b, f = sched
            slot, _ = classify(a or b, field)
            out.append(Segment(_new_id(field, slot, existing), field, slot,
                               f"{a} → {b} @{f:g}", 1.0, origin=origin,
                               schedule=[a, b, f]))
            continue
        body, weight = strip_weight(raw)
        if not body:
            continue
        slot, _how = classify(body, field)
        out.append(Segment(_new_id(field, slot, existing), field, slot, body,
                           weight, origin=origin))
    return out


A1111_TRAILER = re.compile(
    r"^\s*(steps|cfg scale|cfg|sampler|scheduler|model|seed|width|height|size)\s*:",
    re.I | re.M)


def parse_a1111(block: str) -> dict:
    """Split a pasted A1111 block into positive / negative / render settings."""
    neg_split = re.split(r"^\s*negative prompt\s*:", block, maxsplit=1,
                         flags=re.I | re.M)
    positive = neg_split[0].strip()
    negative, trailer = "", ""
    if len(neg_split) > 1:
        rest = neg_split[1]
        m = A1111_TRAILER.search(rest)
        if m:
            negative, trailer = rest[:m.start()].strip(), rest[m.start():]
        else:
            negative = rest.strip()
    else:
        m = A1111_TRAILER.search(positive)
        if m:
            positive, trailer = positive[:m.start()].strip(), positive[m.start():]

    render: dict = {}
    for key, val in re.findall(r"([A-Za-z ]+?)\s*:\s*([^,\n]+)", trailer):
        k = key.strip().lower().replace(" ", "_")
        v = val.strip()
        k = {"cfg_scale": "cfg", "sampler_name": "sampler"}.get(k, k)
        if k in ("steps", "seed", "width", "height"):
            try:
                render[k] = int(v)
            except ValueError:
                pass
        elif k == "cfg":
            try:
                render[k] = float(v)
            except ValueError:
                pass
        elif k == "size":
            if "x" in v:
                w, _, h = v.partition("x")
                try:
                    render["width"], render["height"] = int(w), int(h)
                except ValueError:
                    pass
        elif k in ("sampler", "scheduler", "model"):
            render[k] = v
    return {"positive": positive, "negative": negative, "render": render}


def parse_prompt(block: str, character_hint: str = "") -> tuple[list[Segment], dict]:
    """Full import. Positive phrases are routed to character vs style by slot."""
    parts = parse_a1111(block)
    existing: set[str] = set()
    segs: list[Segment] = []

    for raw in split_top_level(parts["positive"]):
        if raw.strip() == "BREAK":
            segs.append(Segment(_new_id("style", "misc", existing), "style",
                                "misc", BREAK))
            continue
        sched = parse_schedule(raw)
        if sched:
            a, b, f = sched
            slot_c, how_c = classify(a or b, "character")
            fld, slot = (("character", slot_c) if how_c == "lexicon"
                         else ("style", classify(a or b, "style")[0]))
            segs.append(Segment(_new_id(fld, slot, existing), fld, slot,
                                f"{a} → {b} @{f:g}", 1.0, schedule=[a, b, f]))
            continue
        body, weight = strip_weight(raw)
        if not body:
            continue
        # decide the owning field by which field's lexicon claims it
        slot_c, how_c = classify(body, "character")
        slot_s, how_s = classify(body, "style")
        if how_c == "lexicon":
            fld, slot = "character", slot_c
        elif how_s == "lexicon":
            fld, slot = "style", slot_s
        elif how_c == "heuristic":
            fld, slot = "character", slot_c
        elif how_s == "heuristic":
            fld, slot = "style", slot_s
        else:
            fld, slot = "style", "misc"
        segs.append(Segment(_new_id(fld, slot, existing), fld, slot, body, weight))

    segs += parse_field(parts["negative"], "negative", existing)
    return segs, parts["render"]


# --------------------------------------------------------------------------- #
# Compiler:  [Segment] -> string
# --------------------------------------------------------------------------- #

def escape(text: str) -> str:
    return text.replace("(", r"\(").replace(")", r"\)")


def render_segment(s: Segment) -> str:
    if s.text == BREAK:
        return "BREAK"
    if s.schedule:
        a, b, f = s.schedule
        return f"[{escape(str(a))}:{escape(str(b))}:{f:g}]"
    body = escape(s.text)
    if abs(s.weight - 1.0) < 1e-9:
        return body
    w = f"{s.weight:.2f}".rstrip("0").rstrip(".")
    return f"({body}:{w})"


# --------------------------------------------------------------------------- #
# Token budget — the guide's 75-token CLIP chunk. Approximate but honest: CLIP's
# BPE splits unknown words into sub-words, so we over-count long/rare words.
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> int:
    n = 0
    for word in re.findall(r"[A-Za-z0-9']+", text):
        n += 1 if len(word) <= 6 else 1 + (len(word) - 1) // 6
    n += text.count(",")            # punctuation carries a token in CLIP
    return n


def chunk_report(text: str) -> dict:
    """Where the 75-token chunk boundaries fall, and which phrase straddles one."""
    toks, boundary_at, running = estimate_tokens(text), [], 0
    for phrase in split_top_level(text):
        prev = running
        running += estimate_tokens(phrase) + 1
        if prev // 75 != running // 75:
            boundary_at.append(phrase)
    return {"tokens": toks, "chunks": max(1, -(-toks // 75)),
            "straddling": boundary_at}


def compile_field(segs: list[Segment], field: str, order: bool = True) -> str:
    rows = [s for s in segs if s.field == field and s.enabled]
    if order:
        idx = {slot: i for i, slot in enumerate(FIELD_SLOTS[field])}
        rows = sorted(rows, key=lambda s: idx.get(s.slot, 99))
    return ", ".join(render_segment(s) for s in rows)


def compile_all(segs: list[Segment], order: bool = True) -> dict[str, str]:
    return {f: compile_field(segs, f, order) for f in ("character", "style", "negative")}


# --------------------------------------------------------------------------- #
# Edit script
# --------------------------------------------------------------------------- #

WEIGHT_MIN, WEIGHT_MAX, MAX_OPS = 0.5, 1.6, 12
VALID_OPS = {"add", "replace", "remove", "set_weight", "disable", "enable",
             "render", "note"}


def segment_view(segs: list[Segment], render: dict) -> str:
    """The compact numbered view the model is shown."""
    lines = []
    for fld in ("character", "style", "negative"):
        rows = [s for s in segs if s.field == fld]
        if not rows:
            continue
        lines.append(fld.upper())
        idx = {slot: i for i, slot in enumerate(FIELD_SLOTS[fld])}
        for s in sorted(rows, key=lambda s: idx.get(s.slot, 99)):
            w = "" if abs(s.weight - 1.0) < 1e-9 else f" ({s.weight:g})"
            flags = ("  [LOCKED]" if s.locked else "") + ("  [off]" if not s.enabled else "")
            lines.append(f" [{s.id}] {s.slot}: {s.text}{w}{flags}")
    if render:
        lines.append("RENDER")
        lines.append(" " + ", ".join(f"{k}={v}" for k, v in render.items()))
    return "\n".join(lines)


def _norm_id(raw: Any) -> str:
    """Models copy the id straight out of the `[sty.set.1]` view, brackets and all.

    Measured: EVERY model tested did this at least once. Rejecting it would be
    pedantry that reads as model failure, so the validator normalises instead.
    """
    s = str(raw or "").strip()
    while s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s


_RENDER_INT = {"steps", "width", "height", "seed"}
_RENDER_FLOAT = {"cfg"}


def _coerce_render(key: str, value: Any) -> Any:
    """Schema-constrained decoding forces render values to strings ('50'). Coerce."""
    if key in _RENDER_INT:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return value
    if key in _RENDER_FLOAT:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return value
    return value


def apply_ops(segs: list[Segment], render: dict, ops: list[dict]) -> tuple[
        list[Segment], dict, list[str], list[str]]:
    """Validate + apply. Returns (segs, render, applied_log, rejected_log)."""
    segs = [Segment(**asdict(s)) for s in segs]          # deep copy
    render = dict(render)
    by_id = {s.id: s for s in segs}
    existing = set(by_id)
    applied: list[str] = []
    rejected: list[str] = []

    if len(ops) > MAX_OPS:
        rejected.append(f"op cap: {len(ops)} ops > {MAX_OPS}, dropped the tail")
        ops = ops[:MAX_OPS]

    for op in ops:
        kind = str(op.get("op", "")).lower()
        if kind not in VALID_OPS:
            rejected.append(f"unknown op {kind!r}")
            continue
        if kind == "note":
            applied.append(f"note: {op.get('text', '')}")
            continue
        if kind == "render":
            k, v = str(op.get("key", "")).strip().lower(), op.get("value")
            k = {"cfg_scale": "cfg", "sampler_name": "sampler"}.get(k, k)
            if k in ("steps", "cfg", "sampler", "scheduler", "width", "height", "seed"):
                v = _coerce_render(k, v)
                if render.get(k) == v:
                    rejected.append(f"render {k}: no-op (already {v!r})")
                    continue
                applied.append(f"render {k}: {render.get(k)!r} -> {v!r}")
                render[k] = v
            else:
                rejected.append(f"render: unknown key {k!r}")
            continue
        if kind == "add":
            slot = normalise_slot(op.get("slot", "misc"))
            fld = op.get("field") or SLOT_FIELD.get(slot)
            if fld not in FIELD_SLOTS:
                rejected.append(f"add: cannot place slot {slot!r}")
                continue
            if slot not in FIELD_SLOTS[fld]:
                rejected.append(f"add: slot {slot!r} not in {fld}, routed to misc")
                slot = "misc"
            text = strip_slot_prefix(str(op.get("text", "")).strip(), slot)
            if not text:
                rejected.append("add: empty text")
                continue
            w = op.get("weight", 1.0)
            try:
                w = min(WEIGHT_MAX, max(WEIGHT_MIN, round(float(w), 2)))
            except (TypeError, ValueError):
                w = 1.0
            sid = _new_id(fld, slot, existing)
            s = Segment(sid, fld, slot, text, w, origin="ai")
            segs.append(s)
            by_id[sid] = s
            applied.append(f"add {fld}/{slot}: {text!r}" + (f" @{w}" if w != 1.0 else ""))
            continue

        # ops that target an existing id
        sid = _norm_id(op.get("id"))
        s = by_id.get(sid)
        if s is None:
            rejected.append(f"{kind}: unknown id {sid!r}")
            continue
        if s.locked:
            rejected.append(f"{kind}: {sid} is LOCKED ({s.slot})")
            continue
        if kind == "replace":
            text = strip_slot_prefix(str(op.get("text", "")).strip(), s.slot)
            if not text:
                rejected.append(f"replace: empty text for {sid}")
                continue
            if text == s.text:
                rejected.append(f"replace {sid}: no-op (text unchanged)")
                continue
            applied.append(f"replace {sid}: {s.text!r} -> {text!r}")
            s.text = text
            s.origin = "ai"
        elif kind == "remove":
            segs = [x for x in segs if x.id != sid]
            applied.append(f"remove {sid}: {s.text!r}")
        elif kind == "set_weight":
            try:
                w = min(WEIGHT_MAX, max(WEIGHT_MIN, round(float(op.get("weight")), 2)))
            except (TypeError, ValueError):
                rejected.append(f"set_weight: bad weight for {sid}")
                continue
            applied.append(f"weight {sid}: {s.weight:g} -> {w:g}")
            s.weight = w
        elif kind == "disable":
            s.enabled = False
            applied.append(f"disable {sid}: {s.text!r}")
        elif kind == "enable":
            s.enabled = True
            applied.append(f"enable {sid}: {s.text!r}")
    return segs, render, applied, rejected


def dumps(segs: list[Segment]) -> str:
    return json.dumps([asdict(s) for s in segs], indent=1)
