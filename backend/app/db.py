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
"""


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
        if "train_started_at" not in cols:  # pre-0.6.2
            conn.execute("ALTER TABLE projects ADD COLUMN train_started_at REAL NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN train_steps INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN last_train_seconds REAL NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE projects ADD COLUMN last_train_steps INTEGER NOT NULL DEFAULT 0")

        vcols = {r["name"] for r in conn.execute("PRAGMA table_info(prompt_versions)")}
        if "style_lora" not in vcols:  # pre-0.7.9
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN style_lora TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN style_lora_strength REAL NOT NULL DEFAULT 1.0")
        if "lora_stack_json" not in vcols:  # pre-0.8.0
            conn.execute("ALTER TABLE prompt_versions ADD COLUMN lora_stack_json TEXT NOT NULL DEFAULT '[]'")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
