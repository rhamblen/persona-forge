"""SQLite store for projects and the prompt version history.

The version table is append-only: an edit never mutates a row, it inserts a child.
That is what makes rollback safe and guarantees a signed-off prompt can't be lost.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_DIR = Path(os.getenv("DB_DIR", "/data/db"))
DB_PATH = DB_DIR / "persona_forge.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    current_version_id INTEGER,
    -- set when this persona was cloned from another; lets Phase C offer
    -- "reuse parent LoRA" instead of retraining an identical character
    parent_project_id  INTEGER REFERENCES projects(id),
    -- how many selected images the dataset is aiming for (Phase B)
    dataset_target     INTEGER NOT NULL DEFAULT 20,
    -- unique token the trained LoRA binds to; '' means derive pf_<slug> (Phase C)
    trigger_word       TEXT NOT NULL DEFAULT '',
    -- LoRA training state (Phase C, 0.5.2)
    train_prompt_id    TEXT NOT NULL DEFAULT '',
    train_status       TEXT NOT NULL DEFAULT 'none',   -- none | training | done | error
    -- Training timing (0.6.2): start clock of the in-progress/most-recent run and the
    -- duration+steps of the last COMPLETED run — the reference for the next run's ETA.
    train_started_at   REAL NOT NULL DEFAULT 0,        -- epoch secs when the current run started (0 = none)
    train_steps        INTEGER NOT NULL DEFAULT 0,     -- steps of the current/most-recent run
    last_train_seconds REAL NOT NULL DEFAULT 0,        -- wall-clock duration of the last completed run
    last_train_steps   INTEGER NOT NULL DEFAULT 0,     -- steps of the last completed run
    -- Phase 6 (0.6.2): the trained LoRA to load into pose renders. Bare filename
    -- from <slug>/lora/*.safetensors; '' = render poses from the base prompt (no LoRA).
    pose_lora          TEXT NOT NULL DEFAULT '',
    pose_lora_strength REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES prompt_versions(id),
    character   TEXT NOT NULL DEFAULT '',
    style       TEXT NOT NULL DEFAULT '',
    negative    TEXT NOT NULL DEFAULT '',
    checkpoint  TEXT NOT NULL DEFAULT '',
    seed        INTEGER NOT NULL DEFAULT 0,
    -- Prompt Studio (0.7.9): optional external style/detail LoRA applied on top of the
    -- checkpoint via a full LoraLoader. '' = render checkpoint-only (base-character).
    style_lora          TEXT NOT NULL DEFAULT '',
    style_lora_strength REAL NOT NULL DEFAULT 1.0,
    -- Phase H1b (0.8.0): the concept-LoRA stack overlaid on top of the character/style
    -- LoRA — pose/gesture/expression LoRAs that teach the body what to do. Stored as
    -- JSON on the version rather than a child table precisely because versions are
    -- append-only: the stack then rolls back with the prompt for free. A list of
    -- {lora_name, strength_model, strength_clip, enabled, triggers}; `lora_name` is the
    -- file, denormalised so a stack survives its library entry being deleted.
    lora_stack_json     TEXT NOT NULL DEFAULT '[]',
    -- 'manual' | 'ollama' | 'initial'
    source      TEXT NOT NULL DEFAULT 'manual',
    note        TEXT NOT NULL DEFAULT '',
    signed_off  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_project ON prompt_versions(project_id);

CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version_id  INTEGER REFERENCES prompt_versions(id),
    filename    TEXT NOT NULL,
    subfolder   TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'preview',   -- preview | dataset | sprite
    selected    INTEGER NOT NULL DEFAULT 0,        -- dataset cherry-pick flag
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id);

-- Phase B: one row per queued dataset image, reconciled against ComfyUI history
-- as prompts finish. Survives a restart so an in-flight batch isn't lost.
CREATE TABLE IF NOT EXISTS dataset_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt_id   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dsjobs_project ON dataset_jobs(project_id, status);

-- Phase D: one row per pose/expression in a project's set. `modifier` is the
-- per-pose prompt suffix (e.g. "sitting, relaxed"); the image is (re)generated from
-- the project prompt + this modifier. prompt_id/status track an in-flight render.
CREATE TABLE IF NOT EXISTS poses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    modifier    TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    subfolder   TEXT NOT NULL DEFAULT '',
    prompt_id   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'empty',   -- empty | pending | done | error
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_poses_project ON poses(project_id, position);

-- Phase D (0.6.1): one row per pose being exported to a transparent sprite. Each row
-- is a BEN2 background-removal render queued in ComfyUI, reconciled from history as it
-- finishes. `target_name` is the SillyTavern filename the sprite is saved under.
CREATE TABLE IF NOT EXISTS export_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pose_id     INTEGER,
    prompt_id   TEXT NOT NULL,
    target_name TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    subfolder   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_exportjobs_project ON export_jobs(project_id, status);

-- Phase 7 (0.7.0): the generic background job engine. One row per queued/running
-- pipeline (kind='lora_build' now; 'lorebook'/'campaign'/'ingest' later). The worker
-- advances the running job stage-by-stage, reconciling against ComfyUI history, so a
-- build finishes unattended with the browser closed. `state_json` is the handler's
-- scratch (e.g. the training prompt_id) and survives a container restart, so an
-- in-flight job resumes rather than restarting. See jobs.py.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',    -- queued | running | done | error | canceled
    stage       TEXT NOT NULL DEFAULT '',          -- handler-defined current stage label
    params_json TEXT NOT NULL DEFAULT '{}',        -- inputs (steps, rank, preset, ...)
    state_json  TEXT NOT NULL DEFAULT '{}',        -- handler scratch (prompt ids, flags) — resume-safe
    message     TEXT NOT NULL DEFAULT '',          -- human-readable status / last error
    progress    REAL NOT NULL DEFAULT 0,           -- 0..1, optional
    result_json TEXT NOT NULL DEFAULT '{}',        -- outputs (lora filename, pose counts, ...)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    started_at  REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, id);

-- Phase H1b (0.8.0): the concept-LoRA library. Third-party pose/gesture/expression
-- LoRAs ("arm movement", "sitting positions") that carry *what the body is doing*, as
-- opposed to the per-character LoRA that carries *who*. Global, not per project — the
-- whole point is that one is reused across every character.
--
-- `base_model` is compatibility, not decoration: a LoRA only loads on the checkpoint family
-- it was trained for (an SD1.5 LoRA will not load on an SDXL one). Free text, because the
-- project is not committed to a single base model — the library is expected to hold entries
-- for several. `trigger_words` are appended to the positive prompt
-- when the entry is enabled — most concept LoRAs are inert without them.
CREATE TABLE IF NOT EXISTS concept_loras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    filename      TEXT NOT NULL,                     -- as ComfyUI lists it under models/loras
    base_model    TEXT NOT NULL DEFAULT '',          -- '' = unknown/unchecked
    category      TEXT NOT NULL DEFAULT 'pose',      -- pose | gesture | expression | style
    trigger_words TEXT NOT NULL DEFAULT '',
    weight_min    REAL NOT NULL DEFAULT 0.4,
    weight_max    REAL NOT NULL DEFAULT 0.8,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_loras_file ON concept_loras(filename);

-- Phase H1a (0.8.2): the emotion map — axes (*which* emotion) × tiers (*how much*).
-- Seeded from the shipped default in main.py on first boot, then fully editable: the
-- default is a starting point, not a fixed vocabulary. Stored as two tables rather than
-- a JSON blob so axes and tiers get stable ids for rename/reorder/delete, and so a tier
-- label can carry a UNIQUE constraint (it becomes a sprite filename, so collisions
-- would silently overwrite an export).
--
-- `graded` marks an axis that is a real intensity ladder (annoyance→anger→fury) as
-- opposed to a grouping of unrelated states (confusion/curiosity/surprise). Later
-- enrichment should only offer "hone the intensity" on graded axes.
CREATE TABLE IF NOT EXISTS emotion_axes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    axis     TEXT NOT NULL UNIQUE,              -- slug, e.g. 'anger'
    label    TEXT NOT NULL,                     -- display, e.g. 'Anger'
    position INTEGER NOT NULL DEFAULT 0,
    graded   INTEGER NOT NULL DEFAULT 1
);

-- `label` is the SillyTavern expression / sprite filename stem, hence UNIQUE.
-- `builtin` = one of ST's own 28 GoEmotions labels, so the UI can warn before removing
-- one (ST's classifier can still emit it; a missing sprite falls back to neutral). Its
-- inverse — a "custom" tier ST will never classify, needing the Phase H2 state engine to
-- ever fire — is derived rather than stored, so the two can't drift apart.
CREATE TABLE IF NOT EXISTS emotion_tiers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    axis_id  INTEGER NOT NULL REFERENCES emotion_axes(id) ON DELETE CASCADE,
    label    TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0,        -- 1-based tier within the axis
    modifier TEXT NOT NULL DEFAULT '',          -- prose prompt suffix (face AND posture)
    builtin  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_emotion_tiers_axis ON emotion_tiers(axis_id, position);

-- Phase H3 (0.8.4): the ControlNet registry. Same shape and same reason as
-- `concept_loras` — a ControlNet is bound to a checkpoint family, so `base_model` is
-- compatibility rather than decoration, and the project is deliberately not committed to
-- one family. Global, not per project: a ControlNet is character-agnostic.
--
-- `kind` is the control type ('openpose' here; depth/canny later), because a union
-- ControlNet needs `SetUnionControlNetType` told which mode to run and a dedicated one
-- does not.
CREATE TABLE IF NOT EXISTS controlnets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,                  -- as ComfyUI lists it under models/controlnet
    base_model  TEXT NOT NULL DEFAULT '',       -- '' = unknown/unchecked
    kind        TEXT NOT NULL DEFAULT 'openpose',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_controlnets_file ON controlnets(filename);

-- Phase H3b (0.8.5): the pose library. Global, not per persona — a skeleton is
-- character-agnostic, which is the whole reason it is worth curating one set.
--
-- **Keypoints are the source of truth; the PNG is derived.** They are stored normalised
-- 0..1 so one entry renders at any target resolution, and an entry can be edited later
-- without re-authoring. ComfyUI can save POSE_KEYPOINT but has no node that loads it back,
-- so rendering happens app-side (`skeleton.py`).
--
-- `prompt_hint` carries what a skeleton cannot encode: joint positions describe a grip but
-- say nothing about the sword being gripped, and the two must reach the render together or
-- the figure holds thin air. `face_visible` is false for poses that hide or turn away the
-- face, where FaceDetailer would either no-op or repaint a hand into a mess.
CREATE TABLE IF NOT EXISTS pose_library (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'standing',  -- standing | grounded | props | monster
    framing        TEXT NOT NULL DEFAULT 'full',      -- full | cowboy | bust
    keypoints_json TEXT NOT NULL,                     -- 18 normalised [x,y] or null entries
    prompt_hint    TEXT NOT NULL DEFAULT '',
    prop_slot      TEXT NOT NULL DEFAULT '',          -- 'sword' | 'book' | '' — reusable grip
    face_visible   INTEGER NOT NULL DEFAULT 1,
    source         TEXT NOT NULL DEFAULT 'builtin',   -- builtin | imported | harvested | edited
    parent_id      INTEGER REFERENCES pose_library(id),   -- lineage when edited from another
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pose_library_cat ON pose_library(category, name);

-- Which posture FAMILY an emotion axis poses in (Phase H3h, 0.8.10).
--
-- `category` is the coarse UI filter (standing | grounded); `family` is the posture class a
-- mood is actually assigned to — standing, crouching, kneeling, sitting, lying. The point is that an
-- intensity ladder often changes posture as it climbs: annoyance and fury both stand, but
-- sorrow stands where despair sits on the floor. A row with tier IS NULL is the axis-wide
-- default; a row naming a tier overrides just that rung, which is what lets one axis start
-- standing and finish sitting.
CREATE TABLE IF NOT EXISTS axis_pose_families (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    -- NULL = the shipped/global default; a project id = that persona's own override. Two
    -- characters should NOT strike the same pose for the same emotion, so the assignment
    -- has to be per-persona, with the global row as the starting point it diverges from.
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    axis    TEXT NOT NULL,
    tier    INTEGER,                                  -- NULL = the whole axis
    family  TEXT NOT NULL,
    -- Optional: pin this axis/tier to ONE library entry instead of spreading across the
    -- family. The spread is deliberately blind (a name hash), which is fine for variety
    -- and wrong for meaning — "hugging knees, head buried" fits Grief and ruins Elation.
    -- Naming the entry is how a tier gets the figure it actually wants.
    entry_id INTEGER REFERENCES pose_library(id),
    UNIQUE (project_id, axis, tier)
);
"""

# Per-pose ControlNet + face-pass columns (Phase H3, 0.8.4). Nullable on purpose: NULL
# means "inherit the persona's default", so changing the persona-level dial moves every
# pose that hasn't been individually overridden. See docs/pose-control.md §4.1.
_POSE_H3_COLUMNS = [
    # The skeleton driving this pose, as a ComfyUI *input* path. '' = no ControlNet,
    # render prompt-only exactly as before H3.
    ("skeleton_ref", "TEXT NOT NULL DEFAULT ''"),
    # Phase H3b: which library entry produced `skeleton_ref`. Provenance, so the grid can
    # show what a pose was built from and re-render it at a different size.
    ("pose_library_id", "INTEGER"),
    # Per-pose seed. Before H3 every pose in a set rendered at the version's single seed,
    # which is most of why they came out looking the same (docs/pose-control.md §0a).
    ("seed", "INTEGER NOT NULL DEFAULT 0"),
    # Pass 1's output, kept so the face pass can be re-run without re-rendering the body.
    ("base_filename", "TEXT NOT NULL DEFAULT ''"),
    ("base_subfolder", "TEXT NOT NULL DEFAULT ''"),
    # Overrides; NULL = inherit from the project.
    ("cn_strength", "REAL"),
    ("face_pass", "INTEGER"),
    ("face_denoise", "REAL"),
]

# Persona-level pose-render defaults (Phase H3, 0.8.4).
_PROJECT_H3_COLUMNS = [
    ("pose_controlnet", "TEXT NOT NULL DEFAULT ''"),        # filename from the registry
    # 0.8.9: measured up from 0.7/0.7. At 0.7 strength ending at 0.7 the skeleton was too
    # weak to overrule a strength-1.0 character LoRA — an A/B on the same seed showed the
    # pose simply ignored, which read as "ControlNet doesn't work". 1.0/0.9 obeys it.
    ("pose_cn_strength", "REAL NOT NULL DEFAULT 1.0"),
    ("pose_cn_start", "REAL NOT NULL DEFAULT 0.0"),
    # Held just short of 1.0: the last steps free of the skeleton let the character LoRA
    # settle identity, and stop the skeleton's black background bleeding into the frame.
    ("pose_cn_end", "REAL NOT NULL DEFAULT 0.9"),
    ("pose_skeleton", "TEXT NOT NULL DEFAULT ''"),          # default skeleton for the set
    ("pose_library_id", "INTEGER"),                         # which library entry it came from
    ("pose_face_pass", "INTEGER NOT NULL DEFAULT 1"),
    # 0.60 measured, not guessed: 0.45 barely moves an expression and 0.75 destroys the
    # face. See docs/pose-control.md §4.0.
    ("pose_face_denoise", "REAL NOT NULL DEFAULT 0.6"),
]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # lightweight migrations for older databases
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        if "parent_project_id" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN parent_project_id INTEGER")
        if "dataset_target" not in cols:  # pre-0.4.0
            conn.execute("ALTER TABLE projects ADD COLUMN dataset_target INTEGER NOT NULL DEFAULT 20")
        if "trigger_word" not in cols:  # pre-0.5.0
            conn.execute("ALTER TABLE projects ADD COLUMN trigger_word TEXT NOT NULL DEFAULT ''")
        if "train_prompt_id" not in cols:  # pre-0.5.2
            conn.execute("ALTER TABLE projects ADD COLUMN train_prompt_id TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE projects ADD COLUMN train_status TEXT NOT NULL DEFAULT 'none'")
        if "pose_lora" not in cols:  # pre-0.6.2
            conn.execute("ALTER TABLE projects ADD COLUMN pose_lora TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE projects ADD COLUMN pose_lora_strength REAL NOT NULL DEFAULT 1.0")
        # 0.8.9: lift personas still sitting on the old, too-weak ControlNet defaults.
        # A column DEFAULT only applies to new rows, so without this every existing persona
        # keeps 0.7/0.7 and the fix looks like it did nothing. Only the exact old pair is
        # touched — anyone who tuned these deliberately keeps their values.
        if "pose_cn_strength" in cols:
            conn.execute(
                "UPDATE projects SET pose_cn_strength = 1.0, pose_cn_end = 0.9 "
                "WHERE pose_cn_strength = 0.7 AND pose_cn_end = 0.7")
        if "train_started_at" not in cols:  # pre-0.6.2
            conn.execute("ALTER TABLE projects ADD COLUMN train_started_at REAL NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN train_steps INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN last_train_seconds REAL NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN last_train_steps INTEGER NOT NULL DEFAULT 0")

        # 0.8.10: posture family per library entry. Backfilled from the name prefix, which
        # is how the shipped catalogue already encodes it ("Kneeling — upright"); anything
        # unrecognised falls back to its category so no row is left without a family.
        lcols = {r["name"] for r in conn.execute("PRAGMA table_info(pose_library)")}
        if "family" not in lcols:
            conn.execute("ALTER TABLE pose_library ADD COLUMN family TEXT NOT NULL DEFAULT ''")
        conn.execute("""
            UPDATE pose_library SET family = CASE
                WHEN name LIKE 'Crouching%'  THEN 'crouching'
                WHEN name LIKE 'Kneeling%'   THEN 'kneeling'
                WHEN name LIKE 'Sitting%'    THEN 'sitting'
                WHEN name LIKE 'Lying%'      THEN 'lying'
                WHEN name LIKE 'Standing%'   THEN 'standing'
                WHEN category = 'grounded'   THEN 'sitting'
                ELSE 'standing' END
            WHERE family = ''""")

        acols = {r["name"] for r in conn.execute("PRAGMA table_info(axis_pose_families)")}
        if acols and "entry_id" not in acols:
            conn.execute("ALTER TABLE axis_pose_families ADD COLUMN entry_id INTEGER")
        if acols and "project_id" not in acols:
            # Pre-per-persona rows become the global defaults, which is what they were.
            conn.execute("ALTER TABLE axis_pose_families ADD COLUMN project_id INTEGER")

        vcols = {r["name"] for r in conn.execute("PRAGMA table_info(prompt_versions)")}
        if "style_lora" not in vcols:  # pre-0.7.9
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN style_lora TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN style_lora_strength REAL NOT NULL DEFAULT 1.0")
        if "lora_stack_json" not in vcols:  # pre-0.8.0
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN lora_stack_json TEXT NOT NULL DEFAULT '[]'")

        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(poses)")}
        if "axis" not in pcols:  # pre-0.8.2 — grouping the grid by emotion axis
            conn.execute("ALTER TABLE poses ADD COLUMN axis TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE poses ADD COLUMN tier INTEGER NOT NULL DEFAULT 0")

        # pre-0.8.4 — ControlNet + face pass. Added column-by-column rather than as a
        # block so a database that picked up only some of them still lands complete.
        for col, decl in _POSE_H3_COLUMNS:
            if col not in pcols:
                conn.execute(f"ALTER TABLE poses ADD COLUMN {col} {decl}")
        for col, decl in _PROJECT_H3_COLUMNS:
            if col not in cols:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {decl}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
