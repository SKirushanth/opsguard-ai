import os
import sqlite3
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / ".local" / "opsguard.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
    service_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY, origin TEXT NOT NULL, local_id TEXT NOT NULL, config TEXT NOT NULL,
    UNIQUE(origin, local_id)
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY, approval_id TEXT UNIQUE NOT NULL, application_id TEXT NOT NULL,
    service_id TEXT NOT NULL, draft TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', resolved_at TEXT
);
"""


@lru_cache(maxsize=16)
def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA)


@contextmanager
def database():
    path = Path(os.getenv("DATABASE_PATH") or DEFAULT_PATH).resolve()
    initialize(path)
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        with db:
            yield db
