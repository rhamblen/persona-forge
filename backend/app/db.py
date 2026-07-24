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
    dataset_target     INTEGER NOT NULL DEFAULT 20
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


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
