"""Runs the rule filter (rules.py) over the jobs stored in the database.

rules.py decides about ONE job. This file applies those decisions to many
jobs, records progress in the `runs` table so the dashboard can show
"120 of 300 checked", and writes a summary to the activity log.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from roleradar.db import log_event, transaction
from roleradar.profiles import Profile
from roleradar.rules import JobText, evaluate

# How many jobs to save at once. Small batches keep the database free for
# the dashboard between batches, and let progress update while it runs.
BATCH = 50


@dataclass
class FilterSummary:
    run_id: int
    checked: int = 0
    passed: int = 0
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def _jobs_to_check(conn: sqlite3.Connection, profile: str, rerun: bool) -> list[sqlite3.Row]:
    if rerun:
        # Everything the filter decided before, so a changed profile.yaml takes effect.
        # Re-posts and jobs already ranked, built or applied are left alone.
        where = ("status = 'new' OR (filter_result IS NOT NULL AND duplicate_of IS NULL "
                 "AND status IN ('passed', 'dropped'))")
    else:
        where = "status = 'new'"
    return conn.execute(
        f"SELECT id, title, company, location, description FROM jobs "
        f"WHERE profile = ? AND ({where}) ORDER BY id",
        (profile,),
    ).fetchall()


def run_filter(conn: sqlite3.Connection, profile: Profile, *, rerun: bool = False) -> FilterSummary:
    jobs = _jobs_to_check(conn, profile.name, rerun)
    with transaction(conn):
        run_id = conn.execute(
            "INSERT INTO runs (profile, kind, total) VALUES (?, 'filter', ?)", (profile.name, len(jobs))
        ).lastrowid
    summary = FilterSummary(run_id=run_id)
    reason_counts: Counter[str] = Counter()

    try:
        for start in range(0, len(jobs), BATCH):
            with transaction(conn):
                for job in jobs[start:start + BATCH]:
                    result = evaluate(
                        JobText(job["title"], job["company"], job["location"], job["description"]),
                        profile.config,
                    )
                    status = "passed" if result.decision == "pass" else "dropped"
                    reason = result.reasons[0].message if result.reasons else None
                    conn.execute(
                        "UPDATE jobs SET status = ?, status_reason = ?, filter_result = ?, "
                        "filtered_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                        (status, reason, json.dumps(result.to_dict()), job["id"]),
                    )
                    summary.checked += 1
                    if result.decision == "pass":
                        summary.passed += 1
                    else:
                        summary.dropped += 1
                        reason_counts.update({f.rule for f in result.reasons})
                conn.execute("UPDATE runs SET done = ? WHERE id = ?", (summary.checked, run_id))
    except Exception as exc:
        with transaction(conn):
            conn.execute(
                "UPDATE runs SET status = 'failed', message = ?, finished_at = datetime('now') WHERE id = ?",
                (str(exc)[:500], run_id),
            )
            log_event(conn, "filter", f"Filter failed: {exc}", profile=profile.name, level="error")
        raise

    summary.reasons = dict(reason_counts.most_common())
    message = f"Filter checked {summary.checked} jobs: {summary.passed} passed, {summary.dropped} dropped"
    with transaction(conn):
        conn.execute(
            "UPDATE runs SET status = 'done', message = ?, finished_at = datetime('now') WHERE id = ?",
            (message, run_id),
        )
        if summary.checked:
            log_event(conn, "filter", message, profile=profile.name, data=summary.__dict__)
    return summary
