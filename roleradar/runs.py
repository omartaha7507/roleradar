"""Runs: one row per execution of a step (filter, rank, build, scrape).

The dashboard reads these rows to show real progress ("12 of 40 ranked")
whoever started the step: a button, the extension, or another step.
"""

from __future__ import annotations

import sqlite3

from roleradar.db import log_event, transaction


def start_run(conn: sqlite3.Connection, profile: str, kind: str, total: int, message: str | None = None) -> int:
    with transaction(conn):
        return conn.execute(
            "INSERT INTO runs (profile, kind, total, message) VALUES (?, ?, ?, ?)",
            (profile, kind, total, message),
        ).lastrowid


def progress(conn: sqlite3.Connection, run_id: int, done: int, message: str | None = None) -> None:
    with transaction(conn):
        if message is None:
            conn.execute("UPDATE runs SET done = ? WHERE id = ?", (done, run_id))
        else:
            conn.execute("UPDATE runs SET done = ?, message = ? WHERE id = ?", (done, message, run_id))


def set_total(conn: sqlite3.Connection, run_id: int, total: int) -> None:
    with transaction(conn):
        conn.execute("UPDATE runs SET total = ? WHERE id = ?", (total, run_id))


def finish(conn: sqlite3.Connection, run_id: int, status: str, message: str, *, profile: str,
           kind: str, level: str = "info", data: dict | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE runs SET status = ?, message = ?, finished_at = datetime('now') WHERE id = ?",
            (status, message, run_id),
        )
        log_event(conn, kind, message, profile=profile, level=level, data=data)


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """At start-up, runs still marked 'running' were cut off by a restart."""
    with transaction(conn):
        return conn.execute(
            "UPDATE runs SET status = 'stopped', message = COALESCE(message || ' ', '') || "
            "'(interrupted: the server stopped)', finished_at = datetime('now') WHERE status = 'running'"
        ).rowcount
