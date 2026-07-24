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
    parent_project_id  INTEGER REFERENCES projects(id)
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
        # lightweight migration for databases created before 0.2.4
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        if "parent_project_id" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN parent_project_id INTEGER")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
