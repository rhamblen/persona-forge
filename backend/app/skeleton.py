"""OpenPose skeleton rendering — keypoints in, ControlNet control image out.

ControlNet needs a *picture* of a skeleton, but the thing worth storing is the
**keypoints**: they are editable, resolution-independent, and a fraction of the size.
ComfyUI can *save* `POSE_KEYPOINT` (`SavePoseKpsAsJsonFile`) but ships no node that
loads it back into a graph, so the drawing happens here and the PNG is a derived
artifact. See `docs/pose-control.md` §1.

The geometry deliberately follows `controlnet_aux`'s `draw_bodypose` — the renderer the
OpenPose ControlNet models were trained against. Limbs are filled ellipses dimmed to 60%,
joints are full-brightness dots, and the colour order is canonical COCO-18. Deviating
from it costs pose adherence, so this is one of the few places in the project where
matching someone else's implementation exactly matters more than writing it our own way.

Keypoints are stored **normalised 0..1** against the canvas, so one library entry renders
at any target resolution (`docs/pose-control.md` §3.1).
"""

from __future__ import annotations

import io
import math
from typing import Any, Iterable

from PIL import Image, ImageDraw

# COCO-18 joint order, as OpenPose emits it.
JOINTS = [
    "nose", "neck",
    "r_shoulder", "r_elbow", "r_wrist",
    "l_shoulder", "l_elbow", "l_wrist",
    "r_hip", "r_knee", "r_ankle",
    "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
]

# Bone list and per-bone colours, in canonical order.
LIMBS = [
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
]

COLORS = [
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170), (255, 0, 85),
]

# The canonical renderer uses a 4px stick on a 512px canvas. Scaling keeps a skeleton
# rendered at 832x1216 as legible to the model as one rendered at 512x512.
_REFERENCE_EDGE = 512
_BASE_STICK = 4
_BASE_JOINT = 4
_LIMB_DIM = 0.6


class SkeletonError(ValueError):
    pass


def _ellipse_polygon(cx: float, cy: float, ax: float, by: float,
                     angle_deg: float, steps: int = 36) -> list[tuple[float, float]]:
    """Points of an ellipse rotated by `angle_deg` — stands in for cv2.ellipse2Poly."""
    rad = math.radians(angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    pts = []
    for i in range(steps):
        t = 2.0 * math.pi * i / steps
        x, y = ax * math.cos(t), by * math.sin(t)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def normalise_points(points: Iterable[Any]) -> list[tuple[float, float] | None]:
    """Coerce stored keypoints into 18 (x, y) pairs or None, validating as we go.

    Accepts `[x, y]`, `[x, y, confidence]` (confidence <= 0 reads as absent, which is
    how OpenPose marks an undetected joint), or null/empty for a missing joint.
    """
    out: list[tuple[float, float] | None] = []
    for p in points:
        if p is None or p == []:
            out.append(None)
            continue
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise SkeletonError(f"keypoint must be [x, y] or null, got {p!r}")
        if len(p) >= 3 and p[2] is not None and float(p[2]) <= 0:
            out.append(None)
            continue
        out.append((float(p[0]), float(p[1])))
    if len(out) != len(JOINTS):
        raise SkeletonError(f"expected {len(JOINTS)} keypoints, got {len(out)}")
    return out


def render(points: Iterable[Any], width: int, height: int,
           stick_scale: float = 1.0) -> Image.Image:
    """Draw a COCO-18 skeleton on black at `width`x`height`.

    `points` are normalised 0..1; a None entry is an absent joint, and any limb with an
    absent end is skipped rather than drawn to the origin.
    """
    if width <= 0 or height <= 0:
        raise SkeletonError("canvas size must be positive")
    pts = normalise_points(points)

    scale = min(width, height) / _REFERENCE_EDGE
    stick = max(2, round(_BASE_STICK * scale * stick_scale))
    joint = max(2, round(_BASE_JOINT * scale * stick_scale))

    px = [(p[0] * width, p[1] * height) if p else None for p in pts]

    # Limbs first on their own canvas: the reference implementation dims the whole
    # limb layer to 60% and *then* stamps joints at full brightness on top.
    limbs = Image.new("RGB", (width, height), (0, 0, 0))
    ld = ImageDraw.Draw(limbs)
    for idx, (a, b) in enumerate(LIMBS):
        pa, pb = px[a], px[b]
        if pa is None or pb is None:
            continue
        dx, dy = pa[0] - pb[0], pa[1] - pb[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        poly = _ellipse_polygon(
            (pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0,
            length / 2.0, stick, math.degrees(math.atan2(dy, dx)),
        )
        ld.polygon(poly, fill=COLORS[idx % len(COLORS)])

    canvas = limbs.point(lambda v: int(v * _LIMB_DIM))
    cd = ImageDraw.Draw(canvas)
    for idx, p in enumerate(px):
        if p is None:
            continue
        cd.ellipse([p[0] - joint, p[1] - joint, p[0] + joint, p[1] + joint],
                   fill=COLORS[idx % len(COLORS)])
    return canvas


def render_png(points: Iterable[Any], width: int, height: int,
               stick_scale: float = 1.0) -> bytes:
    """`render()` straight to PNG bytes, ready for ComfyUI's /upload/image."""
    buf = io.BytesIO()
    render(points, width, height, stick_scale).save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# The starter pose library (docs/pose-control.md §3.5)
#
# All full-body, all authored against a portrait canvas at a **consistent scale and
# footing** — a set whose figures sit at different sizes makes SillyTavern jitter as it
# swaps sprites, so this is a property of the library, not of any one entry.
#
# `framing` records what the skeleton covers, `face_visible` drives the face pass default
# (FaceDetailer cannot find a face that is hidden or turned away, and forcing it there
# repaints a hand into something mangled), and `prompt_hint` carries anything the skeleton
# implies but cannot encode — a grip says nothing about *what* is being held.
# --------------------------------------------------------------------------- #

# Head geometry, sized against the figure rather than eyeballed. A standing figure spanning
# y 0.05..0.95 is ~7.5 heads tall, which puts the head at ~0.118 of canvas height and ~0.125
# of width — so the ears sit at x 0.500 ± 0.058, not the much narrower spread that a first
# pass tends to produce. Getting this wrong makes every pose read as a 10-head giant.
_NOSE = [0.500, 0.115]
_NECK = [0.500, 0.185]
_HEAD_UP = [[0.470, 0.100], [0.530, 0.100],    # eyes
            [0.442, 0.108], [0.558, 0.108]]    # ears

_LEGS_STRAIGHT = [[0.440, 0.505], [0.435, 0.720], [0.430, 0.942],
                  [0.560, 0.505], [0.565, 0.720], [0.570, 0.942]]
_ARMS_RELAXED = [[0.385, 0.195], [0.360, 0.318], [0.350, 0.438],
                 [0.615, 0.195], [0.640, 0.318], [0.650, 0.438]]


def _standing(arms: list[list[float]], head: list[list[float]] | None = None,
              legs: list[list[float]] | None = None,
              nose: list[float] | None = None,
              neck: list[float] | None = None) -> list[list[float]]:
    """A standing figure varying only the parts a pose actually changes."""
    return ([nose or list(_NOSE), neck or list(_NECK)]
            + arms + (legs or _LEGS_STRAIGHT) + (head or _HEAD_UP))


STANDING_NEUTRAL: list[list[float]] = _standing(_ARMS_RELAXED)


STARTER_POSES: list[dict[str, Any]] = [
    {
        "name": "Standing — neutral", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "", "points": STANDING_NEUTRAL,
    },
    {
        "name": "Standing — weight on one hip", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "relaxed contrapposto stance",
        # Hips tilt one way and shoulders the other — the whole point of contrapposto.
        "points": _standing(
            [[0.393, 0.201], [0.370, 0.323], [0.362, 0.443],
             [0.622, 0.191], [0.645, 0.313], [0.652, 0.433]],
            legs=[[0.445, 0.497], [0.452, 0.717], [0.470, 0.942],
                  [0.560, 0.515], [0.548, 0.725], [0.530, 0.947]]),
    },
    {
        "name": "Standing — arms crossed", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "arms folded across the chest",
        # Forearms cross the midline, so each wrist ends up on the far side of the body.
        "points": _standing(
            [[0.385, 0.200], [0.352, 0.333], [0.560, 0.347],
             [0.615, 0.200], [0.648, 0.333], [0.440, 0.363]]),
    },
    {
        "name": "Standing — hands on hips", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "hands on hips",
        "points": _standing(
            [[0.385, 0.195], [0.330, 0.345], [0.425, 0.492],
             [0.615, 0.195], [0.670, 0.345], [0.575, 0.492]]),
    },
    {
        "name": "Standing — arms raised overhead", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "both arms raised above the head",
        "points": _standing(
            [[0.390, 0.212], [0.338, 0.128], [0.372, 0.038],
             [0.610, 0.212], [0.662, 0.128], [0.628, 0.038]],
            head=[[0.470, 0.118], [0.530, 0.118], [0.442, 0.126], [0.558, 0.126]],
            nose=[0.500, 0.133], neck=[0.500, 0.202]),
    },
    {
        "name": "Standing — shrugging", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "shrugging, palms turned up",
        # Shoulders ride up toward the ears and the forearms turn outward.
        "points": _standing(
            [[0.392, 0.166], [0.348, 0.302], [0.298, 0.386],
             [0.608, 0.166], [0.652, 0.302], [0.702, 0.386]],
            head=[[0.470, 0.108], [0.530, 0.108], [0.442, 0.116], [0.558, 0.116]],
            nose=[0.500, 0.123], neck=[0.500, 0.190]),
    },
    {
        "name": "Standing — head down", "category": "standing", "framing": "full",
        "face_visible": False, "prompt_hint": "head bowed, looking down",
        "points": _standing(
            [[0.385, 0.203], [0.362, 0.325], [0.352, 0.445],
             [0.615, 0.203], [0.638, 0.325], [0.648, 0.445]],
            head=[[0.474, 0.152], [0.526, 0.152], [0.446, 0.138], [0.554, 0.138]],
            nose=[0.500, 0.162], neck=[0.500, 0.193]),
    },
    {
        "name": "Standing — covering face", "category": "standing", "framing": "full",
        "face_visible": False, "prompt_hint": "both hands covering the face",
        "points": _standing(
            [[0.388, 0.207], [0.398, 0.318], [0.466, 0.162],
             [0.612, 0.207], [0.602, 0.318], [0.534, 0.162]]),
    },
    {
        "name": "Standing — fists clenched at sides", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "fists clenched at their sides",
        "points": _standing(
            [[0.388, 0.197], [0.372, 0.322], [0.368, 0.462],
             [0.612, 0.197], [0.628, 0.322], [0.632, 0.462]]),
    },
    {
        "name": "Standing — arms flung wide", "category": "standing", "framing": "full",
        "face_visible": True, "prompt_hint": "arms thrown wide open",
        "points": _standing(
            [[0.388, 0.200], [0.290, 0.246], [0.192, 0.160],
             [0.612, 0.200], [0.710, 0.246], [0.808, 0.160]]),
    },
    # Three arms-wide variants that differ by what the FOREARMS do. COCO-18 has no hand or
    # wrist-rotation joint, so a skeleton cannot encode palm facing at all — the elbow-to-
    # wrist vector is the only structural difference available, and the palm direction (with
    # the emotional read that hangs off it) has to travel in `prompt_hint`. That split is
    # why these are three entries rather than one: the silhouettes really are different, and
    # the hints carry what the joints can't say.
    {
        "name": "Standing — arms wide, palms forward", "category": "standing", "framing": "full",
        "face_visible": True,
        "prompt_hint": "arms out to the sides, forearms raised, both palms facing forward, "
                       "warding off, defensive",
        # Elbows dropped and tucked, forearms near-vertical: the "stop, back off" shape.
        "points": _standing(
            [[0.388, 0.200], [0.306, 0.292], [0.286, 0.148],
             [0.612, 0.200], [0.694, 0.292], [0.714, 0.148]]),
    },
    {
        "name": "Standing — arms wide, palms up and out", "category": "standing", "framing": "full",
        "face_visible": True,
        "prompt_hint": "arms held low and open to the sides, both palms turned upward, "
                       "helpless, resigned, imploring",
        # Arms angled DOWN and out — the open-handed "what else can I do" shape.
        "points": _standing(
            [[0.388, 0.200], [0.298, 0.300], [0.222, 0.396],
             [0.612, 0.200], [0.702, 0.300], [0.778, 0.396]]),
    },
    {
        "name": "Standing — arms wide, palms inward", "category": "standing", "framing": "full",
        "face_visible": True,
        "prompt_hint": "elbows flung out wide, forearms angled back in toward the body, "
                       "both palms facing inward, exasperated, frustrated",
        # Elbows wider than the flung-wide pose but wrists brought back IN toward the torso:
        # the "gesturing at themselves in exasperation" shape.
        "points": _standing(
            [[0.388, 0.200], [0.262, 0.264], [0.370, 0.352],
             [0.612, 0.200], [0.738, 0.264], [0.630, 0.352]]),
    },
    {
        "name": "Kneeling — upright", "category": "grounded", "framing": "full",
        "face_visible": True, "prompt_hint": "kneeling upright on both knees",
        # Kneeling is a shorter figure, so the head sits well below where it stands, and
        # the knee — not the ankle — is the ground contact. The lower leg folds back behind
        # the body, where a front view can't see it: the ankles are **absent**, exactly as
        # OpenPose would report an occluded joint. Putting them below the knees instead is
        # what made the first draft of this pose render as an ordinary standing figure.
        "points": [
            [0.500, 0.345], [0.500, 0.415],
            [0.398, 0.428], [0.374, 0.538], [0.366, 0.648],
            [0.602, 0.428], [0.626, 0.538], [0.634, 0.648],
            [0.452, 0.726], [0.444, 0.940], None,
            [0.548, 0.726], [0.556, 0.940], None,
            [0.470, 0.330], [0.530, 0.330], [0.442, 0.338], [0.558, 0.338],
        ],
    },
    {
        "name": "Kneeling — slumped", "category": "grounded", "framing": "full",
        "face_visible": False, "prompt_hint": "kneeling, slumped, head bowed, arms slack",
        "points": [
            [0.500, 0.432], [0.500, 0.478],
            [0.410, 0.492], [0.386, 0.596], [0.398, 0.716],
            [0.590, 0.492], [0.614, 0.596], [0.602, 0.716],
            [0.454, 0.766], [0.446, 0.944], None,
            [0.546, 0.766], [0.554, 0.944], None,
            [0.472, 0.424], [0.528, 0.424], [0.444, 0.410], [0.556, 0.410],
        ],
    },
    {
        "name": "Sitting — on the floor, legs to one side", "category": "grounded",
        "framing": "full", "face_visible": True,
        "prompt_hint": "sitting on the floor, legs folded to one side",
        # Thigh out to one side, shin folding *back* underneath — without the fold this
        # reads as legs stretched straight out, which is a different pose entirely.
        "points": [
            [0.468, 0.408], [0.476, 0.478],
            [0.394, 0.492], [0.370, 0.604], [0.390, 0.718],
            [0.580, 0.492], [0.602, 0.604], [0.598, 0.712],
            [0.450, 0.848], [0.644, 0.876], [0.556, 0.940],
            [0.540, 0.860], [0.694, 0.912], [0.602, 0.960],
            [0.440, 0.394], [0.498, 0.396], [0.412, 0.402], [0.526, 0.404],
        ],
    },
    {
        "name": "Sitting — hugging knees", "category": "grounded", "framing": "full",
        "face_visible": True, "prompt_hint": "sitting on the floor hugging their knees",
        # Knees come up in *front* of the hips, so knee y is above hip y here, and the feet
        # tuck back in close underneath.
        "points": [
            [0.500, 0.412], [0.500, 0.478],
            [0.418, 0.492], [0.388, 0.608], [0.522, 0.700],
            [0.582, 0.492], [0.612, 0.608], [0.478, 0.700],
            [0.462, 0.878], [0.418, 0.694], [0.478, 0.872],
            [0.542, 0.878], [0.586, 0.694], [0.526, 0.872],
            [0.470, 0.398], [0.530, 0.398], [0.442, 0.406], [0.558, 0.406],
        ],
    },
    {
        "name": "Lying down — on their side", "category": "grounded", "framing": "full",
        "face_visible": True, "prompt_hint": "lying on their side on the ground",
        # A horizontal figure: the spine runs left-to-right rather than top-to-bottom, so
        # it has to be authored across the frame or it renders as a small diagonal smear.
        "points": [
            [0.150, 0.812], [0.258, 0.818],
            [0.268, 0.788], [0.318, 0.876], [0.412, 0.906],
            [0.272, 0.848], [0.344, 0.912], [0.452, 0.900],
            [0.552, 0.816], [0.700, 0.842], [0.856, 0.862],
            [0.558, 0.856], [0.706, 0.886], [0.860, 0.906],
            [0.166, 0.786], [0.206, 0.784], [0.222, 0.804], [0.238, 0.800],
        ],
    },
]
