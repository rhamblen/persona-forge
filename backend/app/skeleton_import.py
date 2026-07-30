"""3D bone positions in, COCO-18 keypoints out — the Blender authoring path (H3g).

Hand-authoring 18 normalised coordinate pairs is where pose entries go wrong, and the errors
are invisible in the numbers: a seated figure written at standing proportions renders as a
standing figure, and a head placed above the neck renders as upright however much the torso
was shortened. Roughly half the entries authored by hand needed a second pass, every failure
caught only by looking at a contact sheet.

Projecting from a *posed 3D armature* removes that class of error by construction — the body
is anatomically consistent because it came from a body. This module is the projection half:
world-space joint positions to normalised 2D keypoints. It deliberately has **no Blender
dependency**, so it is unit-testable offline and Blender's only job is to hand over a dict of
positions (see `docs/pose-control.md` §6.2 for the extraction snippet).

Two properties the library requires and this enforces, which eyeballing does not:

  * **Fixed scale, not per-pose fit.** The world→canvas scale comes from a *reference standing
    height*, never from the current pose's extent. Auto-fitting each pose would inflate a
    seated figure to the same frame height as a standing one, and a set whose figures differ
    in size makes SillyTavern jitter as it swaps sprites.
  * **Anchored footing.** The ground plane maps to a fixed canvas y, so every figure stands,
    sits and kneels on the same floor.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from . import skeleton

# COCO-18 index per joint name, matching skeleton.JOINTS order.
COCO_INDEX = {name: i for i, name in enumerate(skeleton.JOINTS)}

# Bone-name aliases per COCO joint, lowercased and matched loosely. Covers Rigify
# (`shoulder.L`, `forearm_tweak.L`), Mixamo (`mixamorig:LeftArm`) and the plain names a
# hand-built armature tends to use. Order matters — the first alias found wins, so the more
# specific name is listed before the one that would also substring-match it.
BONE_ALIASES: dict[str, tuple[str, ...]] = {
    "nose":       ("nose", "head_tip", "face", "head"),
    "neck":       ("neck", "spine.006", "spine6"),
    "r_shoulder": ("shoulder.r", "upper_arm.r", "rightarm", "r_upperarm", "arm.r"),
    "r_elbow":    ("forearm.r", "rightforearm", "r_forearm", "elbow.r"),
    "r_wrist":    ("hand.r", "righthand", "r_hand", "wrist.r"),
    "l_shoulder": ("shoulder.l", "upper_arm.l", "leftarm", "l_upperarm", "arm.l"),
    "l_elbow":    ("forearm.l", "leftforearm", "l_forearm", "elbow.l"),
    "l_wrist":    ("hand.l", "lefthand", "l_hand", "wrist.l"),
    "r_hip":      ("thigh.r", "rightupleg", "r_thigh", "hip.r", "upleg.r"),
    "r_knee":     ("shin.r", "rightleg", "r_shin", "knee.r", "calf.r"),
    "r_ankle":    ("foot.r", "rightfoot", "r_foot", "ankle.r"),
    "l_hip":      ("thigh.l", "leftupleg", "l_thigh", "hip.l", "upleg.l"),
    "l_knee":     ("shin.l", "leftleg", "l_shin", "knee.l", "calf.l"),
    "l_ankle":    ("foot.l", "leftfoot", "l_foot", "ankle.l"),
    "r_eye":      ("eye.r", "righteye", "r_eye"),
    "l_eye":      ("eye.l", "lefteye", "l_eye"),
    "r_ear":      ("ear.r", "rightear", "r_ear"),
    "l_ear":      ("ear.l", "leftear", "l_ear"),
}


class ImportError_(ValueError):
    """Bone data that can't be projected."""


def match_bones(positions: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Map arbitrary bone names onto COCO joint names.

    Matching is lowercased substring, most-specific-alias-first. Unmatched bones are dropped
    rather than guessed at — a wrong joint is worse than an absent one, because an absent
    joint renders as an occlusion (which OpenPose emits routinely) while a wrong one renders
    as a broken body.
    """
    lowered = {str(k).lower(): k for k in positions}
    out: dict[str, tuple[float, float, float]] = {}
    for joint, aliases in BONE_ALIASES.items():
        for alias in aliases:
            hit = next((orig for low, orig in lowered.items() if alias in low), None)
            if hit is not None:
                p = positions[hit]
                if p is None:
                    break
                if len(tuple(p)) < 3:
                    raise ImportError_(f"bone '{hit}' needs an (x, y, z), got {p!r}")
                out[joint] = (float(p[0]), float(p[1]), float(p[2]))
                break
    return out


def _project_point(p: tuple[float, float, float], cam_dist: float,
                   focal: float, z_eye: float) -> tuple[float, float] | None:
    """Perspective-project one Blender-space point (X right, Y depth, Z up) to camera plane.

    A front camera on -Y looking at +Y. Perspective rather than orthographic on purpose: it is
    what produces genuine foreshortening, so legs extended toward the viewer shorten instead of
    keeping their standing length. That foreshortening is exactly what hand-authoring kept
    getting wrong.
    """
    x, y, z = p
    depth = cam_dist + y          # distance from camera plane; +Y is away from the camera
    if depth <= 1e-6:
        return None               # at or behind the lens — treat as not visible
    return (focal * x / depth, focal * (z - z_eye) / depth)


def project_bones(
    positions: dict[str, Any],
    *,
    ref_height: float = 1.75,
    ground_z: float = 0.0,
    figure_fraction: float = 0.90,
    footing: float = 0.95,
    cam_dist: float = 6.0,
    focal: float = 2.0,
    centre_x: float = 0.5,
) -> list[list[float] | None]:
    """Posed armature -> 18 normalised COCO-18 keypoints, ready for `pose_library`.

    `ref_height` is the character's STANDING height in Blender units — the scale reference, so
    a crouch stays shorter than a stand instead of being fitted to the same frame height.
    `figure_fraction` is how much of the canvas that reference height occupies, and `footing`
    is the canvas y the ground plane lands on. Both are properties of the whole library, not
    of one pose: keep them identical across every entry or the set will jitter in SillyTavern.

    Returns a list usable directly as a library entry's `points`, with `None` for any joint the
    rig didn't supply or that fell behind the camera.
    """
    if ref_height <= 0:
        raise ImportError_("ref_height must be positive")
    if not 0 < figure_fraction <= 1:
        raise ImportError_("figure_fraction must be in (0, 1]")

    matched = match_bones(positions)
    if not matched:
        raise ImportError_("no bones matched any COCO-18 joint — check the rig's bone names")

    # Scale is fixed by the reference height at the ground plane's depth, NOT by this pose's
    # own extent. Project the reference span once and derive units-per-canvas from it.
    z_eye = ground_z + ref_height * 0.5
    top = _project_point((0.0, 0.0, ground_z + ref_height), cam_dist, focal, z_eye)
    bottom = _project_point((0.0, 0.0, ground_z), cam_dist, focal, z_eye)
    if top is None or bottom is None:
        raise ImportError_("the reference height does not project in front of the camera")
    span = abs(top[1] - bottom[1])
    if span <= 1e-9:
        raise ImportError_("degenerate projection — check cam_dist and focal")
    per_canvas = figure_fraction / span          # camera-plane units -> canvas fraction

    ground_v = _project_point((0.0, 0.0, ground_z), cam_dist, focal, z_eye)
    assert ground_v is not None

    out: list[list[float] | None] = [None] * len(skeleton.JOINTS)
    for joint, world in matched.items():
        pt = _project_point(world, cam_dist, focal, z_eye)
        if pt is None:
            continue
        u, v = pt
        # Canvas y grows downward, so a higher world Z gives a smaller y.
        out[COCO_INDEX[joint]] = [
            round(centre_x + u * per_canvas, 4),
            round(footing - (v - ground_v[1]) * per_canvas, 4),
        ]
    return out


def describe(points: Iterable[Any]) -> dict[str, Any]:
    """Sanity figures for a projected pose — the numbers worth eyeballing before saving.

    Not a validator: a pose can be unusual and correct. These are the measurements whose
    surprises have meant a bad entry — a figure taller than the canvas, a head that ended up
    below the neck when it shouldn't be, or so many missing joints that the rig clearly
    didn't match.
    """
    pts = skeleton.normalise_points(points)
    present = [(i, p) for i, p in enumerate(pts) if p is not None]
    if not present:
        return {"joints": 0}
    ys = [p[1] for _, p in present]
    xs = [p[0] for _, p in present]
    nose, neck = pts[COCO_INDEX["nose"]], pts[COCO_INDEX["neck"]]
    hips = [pts[COCO_INDEX[j]] for j in ("r_hip", "l_hip") if pts[COCO_INDEX[j]]]
    return {
        "joints": len(present),
        "missing": [skeleton.JOINTS[i] for i, p in enumerate(pts) if p is None],
        "height_fraction": round(max(ys) - min(ys), 3),
        "x_span": round(max(xs) - min(xs), 3),
        "top_y": round(min(ys), 3),
        "bottom_y": round(max(ys), 3),
        "head_below_neck": bool(nose and neck and nose[1] > neck[1]),
        "hip_y": round(sum(h[1] for h in hips) / len(hips), 3) if hips else None,
        "off_canvas": [skeleton.JOINTS[i] for i, p in present
                       if not (0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0)],
    }
