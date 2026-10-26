"""The database: one SQLite file that holds everything for every profile.

Why SQLite instead of many JSON files:
- One writer at a time is handled by the database, so there are no
  "file is locked" crashes when two parts of the program touch the same data.
- Questions like "how many Berlin jobs were dropped for German this week?"
  become one query instead of a script.
- It is still a single file you can copy, back up or open in any SQLite viewer.

Every table has a `profile` column, so several people share the file without
ever seeing each other's rows: every query filters by profile.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Each entry upgrades the database by one version. Never edit an entry that has
# been released; add a new one instead. SQLite remembers the current version in
# PRAGMA user_version, so each upgrade runs exactly once.
MIGRATIONS: list[str] = [
    # --- version 1 -------------------------------------------------------------
    """
    CREATE TABLE searches (
        id          INTEGER PRIMARY KEY,
        profile     TEXT    NOT NULL,
        label       TEXT    NOT NULL,
        url         TEXT    NOT NULL,
        site        TEXT    NOT NULL,
        stage       INTEGER NOT NULL DEFAULT 1 CHECK (stage BETWEEN 1 AND 9),
        pages       INTEGER NOT NULL DEFAULT 1 CHECK (pages BETWEEN 1 AND 3),
        enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (profile, url)
    );

    CREATE TABLE jobs (
        id             INTEGER PRIMARY KEY,
        profile        TEXT NOT NULL,
        source         TEXT NOT NULL,
        url            TEXT NOT NULL,
        url_key        TEXT NOT NULL,
        role_key       TEXT NOT NULL,
        title          TEXT NOT NULL,
        company        TEXT NOT NULL,
        location       TEXT,
        description    TEXT,
        posted_at      TEXT,
        search_id      INTEGER REFERENCES searches (id) ON DELETE SET NULL,
        status         TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                         ('new', 'passed', 'dropped', 'ranked', 'built',
                          'applied', 'skipped', 'closed')),
        status_reason  TEXT,
        duplicate_of   INTEGER REFERENCES jobs (id) ON DELETE SET NULL,
        first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (profile, url_key)
    );
    CREATE INDEX jobs_by_status ON jobs (profile, status);
    CREATE INDEX jobs_by_role   ON jobs (profile, role_key);

    CREATE TABLE runs (
        id           INTEGER PRIMARY KEY,
        profile      TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('scrape', 'filter', 'rank', 'build')),
        status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                       ('running', 'done', 'failed', 'stopped')),
        total        INTEGER NOT NULL DEFAULT 0,
        done         INTEGER NOT NULL DEFAULT 0,
        message      TEXT,
        started_at   TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at  TEXT
    );
    CREATE INDEX runs_by_profile ON runs (profile, kind, started_at);

    CREATE TABLE events (
        id       INTEGER PRIMARY KEY,
        profile  TEXT,
        at       TEXT NOT NULL DEFAULT (datetime('now')),
        level    TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning', 'error')),
        kind     TEXT NOT NULL,
        message  TEXT NOT NULL,
        data     TEXT
    );
    CREATE INDEX events_by_profile ON events (profile, id);
    """,
    # --- version 2: stage 2, rule filter ----------------------------------------------
    """
    ALTER TABLE jobs ADD COLUMN filter_result TEXT;   -- JSON: decision, reasons, notes
    ALTER TABLE jobs ADD COLUMN filtered_at   TEXT;
    """,
    # --- version 3: stage 3, AI ranker ----------------------------------------------------
    """
    ALTER TABLE jobs ADD COLUMN rank_score  INTEGER;
    ALTER TABLE jobs ADD COLUMN rank_result TEXT;     -- JSON: the model's judgement + score parts
    ALTER TABLE jobs ADD COLUMN ranked_at   TEXT;
    CREATE INDEX jobs_by_score ON jobs (profile, status, rank_score);

    -- The model's answers, so the same job is never asked twice.
    CREATE TABLE rank_cache (
        key         TEXT PRIMARY KEY,
        model       TEXT NOT NULL,
        result      TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # --- version 4: stage 4, Chrome extension ------------------------------------------------
    """
    -- "Please scrape" requests. The dashboard creates them; the extension,
    -- which checks in every minute, picks them up.
    CREATE TABLE scrape_requests (
        id           INTEGER PRIMARY KEY,
        profile      TEXT NOT NULL,
        stage        INTEGER,                    -- only searches of this stage; NULL = all
        status       TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN
                       ('waiting', 'running', 'done', 'failed', 'cancelled')),
        run_id       INTEGER REFERENCES runs (id),
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        claimed_at   TEXT,
        finished_at  TEXT
    );

    -- Small facts the app remembers, e.g. when the extension last checked in.
    CREATE TABLE kv (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # --- version 5: stage 5, CV and cover-letter builder ----------------------------------------
    """
    CREATE TABLE builds (
        id           INTEGER PRIMARY KEY,
        profile      TEXT    NOT NULL,
        job_id       INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
        folder       TEXT    NOT NULL,
        cv_pdf       TEXT,             -- file names inside the folder; NULL when no PDF could be made
        letter_pdf   TEXT,
        language     TEXT    NOT NULL,
        fit_summary  TEXT,
        flags        TEXT,             -- JSON list: gaps worth knowing before applying
        model        TEXT,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX builds_by_job ON builds (profile, job_id);
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may open a request's connection on one worker
    # thread and use it on another. That is safe here because each connection still
    # belongs to exactly one request (or one background task) at a time.
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None,  # autocommit; see transaction()
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the dashboard read while the ranker writes, without blocking each other.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All writes inside happen together, or none of them do."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database up to the newest version. Safe to call every start."""
    current = schema_version(conn)
    for version, script in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        # executescript commits on its own, so the version bump is part of the script.
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
    return schema_version(conn)


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    message: str,
    *,
    profile: str | None = None,
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (profile, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
        (profile, level, kind, message, json.dumps(data) if data else None),
    )
