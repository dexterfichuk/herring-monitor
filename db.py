"""SQLite database setup and connection management."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT DEFAULT '',
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    source TEXT DEFAULT 'manual',
    spawn_season_start TEXT DEFAULT '02-01',
    spawn_season_end TEXT DEFAULT '05-15',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER REFERENCES locations(id),
    scene_id TEXT,
    scene_date TEXT,
    cloud_cover REAL,
    filename TEXT,
    spawn_score REAL,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lat REAL, lon REAL,
    scene_id TEXT, scene_date TEXT,
    cloud_cover REAL,
    filename TEXT,
    knn_score REAL,
    label TEXT,
    labeled_by TEXT,
    labeled_at TEXT,
    promoted_to_location_id INTEGER REFERENCES locations(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_images_location ON images(location_id);
CREATE INDEX IF NOT EXISTS idx_images_scenedate ON images(scene_date);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(knn_score DESC);
"""


def init_db(db_path: Path):
    """Create tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_db(db_path: Path) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
