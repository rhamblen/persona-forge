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


# A single neutral standing pose, used to prove the render path before the pose library
# lands (docs/pose-control.md, H3b). Normalised against a portrait canvas: full figure,
# head near the top, feet just above the bottom edge.
STANDING_NEUTRAL: list[list[float]] = [
    [0.500, 0.100],  # nose
    [0.500, 0.170],  # neck
    [0.385, 0.180], [0.360, 0.305], [0.350, 0.425],   # right arm
    [0.615, 0.180], [0.640, 0.305], [0.650, 0.425],   # left arm
    [0.440, 0.495], [0.435, 0.715], [0.430, 0.940],   # right leg
    [0.560, 0.495], [0.565, 0.715], [0.570, 0.940],   # left leg
    [0.482, 0.090], [0.518, 0.090],                   # eyes
    [0.462, 0.096], [0.538, 0.096],                   # ears
]
