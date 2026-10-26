"""Stage 4, server side: the hand-over between the dashboard and the extension.

The server can't open LinkedIn itself: LinkedIn shows job details only to
signed-in people and blocks bots. So the scraping happens in YOUR Chrome,
through the extension, which already has your LinkedIn login.

How they meet:
1. You press "Scrape" on the dashboard: a request is stored as 'waiting'.
2. The extension checks in every minute (GET /api/extension/next). If a
   request is waiting, it claims it and gets the list of searches to open.
3. It sends the jobs it reads (POST /api/profiles/{name}/jobs), reports
   progress, and finally says it is finished.
The server never calls the extension; the extension always asks. That keeps
the server simple and works even if Chrome was closed for a while.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from roleradar import store
from roleradar.db import log_event, transaction
from roleradar.runs import finish, progress, start_run

STALE_MINUTES = 30  # a claimed request with no finish after this long is given up


class ScrapeError(Exception):
    pass


def touch_extension(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO kv (key, value) VALUES ('extension_seen', datetime('now')) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')"
        )


def extension_last_seen(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = 'extension_seen'").fetchone()
    return row["value"] if row else None


def searches_for(conn: sqlite3.Connection, profile: str, stage: int | None) -> list[dict[str, Any]]:
    searches = [s for s in store.list_searches(conn, profile) if s["enabled"]]
    if stage is not None:
        searches = [s for s in searches if s["stage"] == stage]
    return searches


def request_scrape(conn: sqlite3.Connection, profile: str, stage: int | None = None) -> dict[str, Any]:
    active = conn.execute(
        "SELECT id, status FROM scrape_requests WHERE profile = ? AND status IN ('waiting', 'running')",
        (profile,),
    ).fetchone()
    if active:
        raise ScrapeError(f"A scrape is already {active['status']} for this profile")
    searches = searches_for(conn, profile, stage)
    if not searches:
        raise ScrapeError("No enabled searches" + (f" in stage {stage}" if stage else "")
                          + ". Add a LinkedIn search first.")
    with transaction(conn):
        request_id = conn.execute(
            "INSERT INTO scrape_requests (profile, stage) VALUES (?, ?)", (profile, stage)
        ).lastrowid
        log_event(conn, "scrape", f"Scrape requested: {len(searches)} searches. "
                                  "Waiting for the Chrome extension to pick it up.", profile=profile)
    return {"request_id": request_id, "searches": len(searches)}


def _give_up_stale(conn: sqlite3.Connection) -> None:
    stale = conn.execute(
        "SELECT id, profile, run_id FROM scrape_requests WHERE status = 'running' "
        f"AND claimed_at < datetime('now', '-{STALE_MINUTES} minutes')"
    ).fetchall()
    for row in stale:
        with transaction(conn):
            conn.execute("UPDATE scrape_requests SET status = 'failed', finished_at = datetime('now') "
                         "WHERE id = ?", (row["id"],))
        if row["run_id"]:
            finish(conn, row["run_id"], "failed", "Scrape gave up: the extension stopped reporting",
                   profile=row["profile"], kind="scrape", level="warning")


def claim_next(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Called by the extension every minute. Returns the next job to do, or None."""
    touch_extension(conn)
    _give_up_stale(conn)
    row = conn.execute(
        "SELECT * FROM scrape_requests WHERE status = 'waiting' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        return None
    searches = searches_for(conn, row["profile"], row["stage"])
    run_id = start_run(conn, row["profile"], "scrape", len(searches), "Chrome extension is scraping")
    with transaction(conn):
        conn.execute(
            "UPDATE scrape_requests SET status = 'running', run_id = ?, claimed_at = datetime('now') WHERE id = ?",
            (run_id, row["id"]),
        )
    return {
        "request_id": row["id"],
        "profile": row["profile"],
        "searches": [{"id": s["id"], "label": s["label"], "url": s["url"], "pages": s["pages"]} for s in searches],
    }


def _get_running(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM scrape_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        raise ScrapeError("No such scrape request")
    if row["status"] != "running":
        raise ScrapeError(f"This scrape is {row['status']}, not running")
    return row


def report_progress(conn: sqlite3.Connection, request_id: int, done: int, message: str) -> None:
    row = _get_running(conn, request_id)
    progress(conn, row["run_id"], done, message)


def report_finished(conn: sqlite3.Connection, request_id: int, ok: bool, message: str) -> str:
    row = _get_running(conn, request_id)
    status = "done" if ok else "failed"
    with transaction(conn):
        conn.execute("UPDATE scrape_requests SET status = ?, finished_at = datetime('now') WHERE id = ?",
                     (status, request_id))
    finish(conn, row["run_id"], status, message, profile=row["profile"], kind="scrape",
           level="info" if ok else "warning")
    return row["profile"]


def cancel(conn: sqlite3.Connection, profile: str) -> bool:
    with transaction(conn):
        rows = conn.execute(
            "UPDATE scrape_requests SET status = 'cancelled', finished_at = datetime('now') "
            "WHERE profile = ? AND status IN ('waiting', 'running') RETURNING run_id",
            (profile,),
        ).fetchall()
    for row in rows:
        if row["run_id"]:
            finish(conn, row["run_id"], "stopped", "Scrape cancelled", profile=profile, kind="scrape")
    return bool(rows)


def is_cancelled(conn: sqlite3.Connection, request_id: int) -> bool:
    row = conn.execute("SELECT status FROM scrape_requests WHERE id = ?", (request_id,)).fetchone()
    return not row or row["status"] == "cancelled"


def mark_interrupted_requests(conn: sqlite3.Connection) -> None:
    """A restart loses track of any scrape in progress."""
    with transaction(conn):
        conn.execute("UPDATE scrape_requests SET status = 'failed', finished_at = datetime('now') "
                     "WHERE status IN ('running')")
