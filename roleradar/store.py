"""Reading and writing jobs, searches and events.

The API routes stay short because the actual work lives here, in plain
functions that take a database connection. Plain functions are also easy to
test without starting a web server.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from roleradar.db import log_event, transaction
from roleradar.keys import role_key, site_of, url_key

JOB_STATUSES = ("new", "passed", "dropped", "ranked", "built", "applied", "skipped", "closed")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --- Jobs ---------------------------------------------------------------------


@dataclass
class IngestResult:
    inserted: int = 0
    updated: int = 0
    reposts: int = 0


def ingest_jobs(
    conn: sqlite3.Connection,
    profile: str,
    source: str,
    jobs: Iterable[dict[str, Any]],
    search_id: int | None = None,
) -> IngestResult:
    """Store scraped jobs. Seen before -> refreshed; same role re-posted -> dropped."""
    result = IngestResult()
    with transaction(conn):
        for job in jobs:
            ukey = url_key(job["url"])
            rkey = role_key(job["company"], job["title"])
            description = (job.get("description") or "").strip() or None

            existing = conn.execute(
                "SELECT id, description FROM jobs WHERE profile = ? AND url_key = ?",
                (profile, ukey),
            ).fetchone()
            if existing:
                # A later scrape may have the full description where an earlier
                # one only had the card; keep whichever is longer.
                old = existing["description"] or ""
                better = description if description and len(description) > len(old) else old or None
                conn.execute(
                    "UPDATE jobs SET last_seen_at = datetime('now'), description = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (better, existing["id"]),
                )
                result.updated += 1
                continue

            original = conn.execute(
                "SELECT id, status FROM jobs WHERE profile = ? AND role_key = ? "
                "ORDER BY (status = 'applied') DESC, id LIMIT 1",
                (profile, rkey),
            ).fetchone()
            if original:
                status = "dropped"
                reason = (
                    f"Already applied (job #{original['id']})"
                    if original["status"] == "applied"
                    else f"Re-post of job #{original['id']}"
                )
                result.reposts += 1
            else:
                status, reason = "new", None

            conn.execute(
                """INSERT INTO jobs (profile, source, url, url_key, role_key, title, company,
                                     location, description, posted_at, search_id,
                                     status, status_reason, duplicate_of)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile, source, job["url"].strip(), ukey, rkey,
                    job["title"].strip(), job["company"].strip(),
                    (job.get("location") or "").strip() or None,
                    description, job.get("posted_at"), search_id,
                    status, reason, original["id"] if original else None,
                ),
            )
            result.inserted += 1

        log_event(
            conn, "ingest",
            f"{result.inserted} new jobs from {source} ({result.updated} seen before, "
            f"{result.reposts} re-posts dropped)",
            profile=profile, data=result.__dict__,
        )
    return result


def list_jobs(
    conn: sqlite3.Connection,
    profile: str,
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["profile = ?"]
    args: list[Any] = [profile]
    if status:
        where.append("status = ?")
        args.append(status)
    if search:
        where.append("(title LIKE ? OR company LIKE ?)")
        args += [f"%{search}%", f"%{search}%"]
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {clause}", args).fetchone()[0]
    rows = conn.execute(
        f"""SELECT id, source, url, title, company, location, posted_at, status,
                   status_reason, duplicate_of, first_seen_at, last_seen_at,
                   length(description) AS description_chars
            FROM jobs WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows], total


def get_job(conn: sqlite3.Connection, profile: str, job_id: int) -> dict[str, Any] | None:
    return row_to_dict(
        conn.execute("SELECT * FROM jobs WHERE profile = ? AND id = ?", (profile, job_id)).fetchone()
    )


def status_counts(conn: sqlite3.Connection, profile: str) -> dict[str, int]:
    counts = dict.fromkeys(JOB_STATUSES, 0)
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM jobs WHERE profile = ? GROUP BY status", (profile,)
    ):
        counts[row["status"]] = row["n"]
    return counts


# --- Searches -----------------------------------------------------------------


class DuplicateSearch(Exception):
    pass


def list_searches(conn: sqlite3.Connection, profile: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM searches WHERE profile = ? ORDER BY stage, id", (profile,)
    ).fetchall()
    return [_search_out(r) for r in rows]


def get_search(conn: sqlite3.Connection, profile: str, search_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM searches WHERE profile = ? AND id = ?", (profile, search_id)
    ).fetchone()
    return _search_out(row) if row else None


def create_search(conn: sqlite3.Connection, profile: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO searches (profile, label, url, site, stage, pages, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile, data["label"], data["url"], site_of(data["url"]),
                 data["stage"], data["pages"], int(data["enabled"])),
            )
            log_event(conn, "search", f"Search added: {data['label']}", profile=profile)
    except sqlite3.IntegrityError as exc:
        raise DuplicateSearch(data["url"]) from exc
    return get_search(conn, profile, cur.lastrowid)  # type: ignore[return-value]


def update_search(
    conn: sqlite3.Connection, profile: str, search_id: int, changes: dict[str, Any]
) -> dict[str, Any] | None:
    allowed = {k: v for k, v in changes.items() if k in {"label", "url", "stage", "pages", "enabled"}}
    if "enabled" in allowed:
        allowed["enabled"] = int(allowed["enabled"])
    if "url" in allowed:
        allowed["site"] = site_of(allowed["url"])
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        try:
            with transaction(conn):
                conn.execute(
                    f"UPDATE searches SET {sets} WHERE profile = ? AND id = ?",
                    [*allowed.values(), profile, search_id],
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateSearch(allowed.get("url", "")) from exc
    return get_search(conn, profile, search_id)


def delete_search(conn: sqlite3.Connection, profile: str, search_id: int) -> bool:
    with transaction(conn):
        cur = conn.execute("DELETE FROM searches WHERE profile = ? AND id = ?", (profile, search_id))
    return cur.rowcount > 0


def _search_out(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    return out


# --- Events and runs -------------------------------------------------------------


def list_events(conn: sqlite3.Connection, profile: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE profile = ? OR profile IS NULL ORDER BY id DESC LIMIT ?",
        (profile, limit),
    ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["data"] = json.loads(event["data"]) if event["data"] else None
        events.append(event)
    return events


def latest_runs(conn: sqlite3.Connection, profile: str) -> dict[str, dict[str, Any]]:
    """The most recent run of each kind (scrape, filter, rank, build)."""
    rows = conn.execute(
        """SELECT * FROM runs r WHERE profile = ? AND id = (
               SELECT MAX(id) FROM runs WHERE profile = r.profile AND kind = r.kind)""",
        (profile,),
    ).fetchall()
    return {row["kind"]: dict(row) for row in rows}
