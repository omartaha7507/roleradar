"""Jobs: the extension posts what it scraped; the dashboard reads them back."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from roleradar import store
from roleradar.api.deps import Conn, CurrentProfile
from roleradar.api.schemas import (
    EventOut,
    FilterResultOut,
    FilterRunOut,
    FilterTestIn,
    IngestRequest,
    IngestResponse,
    JobDetail,
    JobPage,
    JobStatus,
    Summary,
)
from roleradar.filtering import run_filter
from roleradar.rules import JobText, evaluate

router = APIRouter(prefix="/api/profiles/{name}", tags=["jobs"])


@router.post("/jobs", response_model=IngestResponse, summary="Add scraped jobs")
def ingest(body: IngestRequest, profile: CurrentProfile, conn: Conn) -> dict:
    if body.search_id is not None and not store.get_search(conn, profile.name, body.search_id):
        raise HTTPException(404, f"Search {body.search_id} does not exist for this profile")
    result = store.ingest_jobs(
        conn, profile.name, body.source,
        (job.model_dump() for job in body.jobs), search_id=body.search_id,
    )
    # New jobs go through the rule filter straight away; it takes milliseconds.
    filtered = run_filter(conn, profile)
    return {**result.__dict__, "passed": filtered.passed, "dropped": filtered.dropped}


@router.post("/filter", response_model=FilterRunOut, summary="Run the rule filter")
def filter_jobs(
    profile: CurrentProfile,
    conn: Conn,
    rerun: Annotated[bool, Query(description="Also re-check jobs filtered before, "
                                             "e.g. after you changed profile.yaml")] = False,
) -> dict:
    return run_filter(conn, profile, rerun=rerun).__dict__


@router.post("/filter/test", response_model=FilterResultOut,
             summary="Try the filter on one job without saving it")
def filter_test(body: FilterTestIn, profile: CurrentProfile) -> dict:
    job = JobText(body.title, body.company, body.location, body.description)
    return evaluate(job, profile.config).to_dict()


@router.get("/jobs", response_model=JobPage, summary="List jobs, newest first")
def jobs(
    profile: CurrentProfile,
    conn: Conn,
    status: JobStatus | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search title or company")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    items, total = store.list_jobs(conn, profile.name, status=status, search=q, limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="One job, with its description")
def job(job_id: int, profile: CurrentProfile, conn: Conn) -> dict:
    found = store.get_job(conn, profile.name, job_id)
    if not found:
        raise HTTPException(404, "Job not found")
    found["description_chars"] = len(found["description"] or "")
    return found


@router.get("/summary", response_model=Summary, summary="Counts for the dashboard")
def summary(profile: CurrentProfile, conn: Conn) -> dict:
    searches = store.list_searches(conn, profile.name)
    return {
        "profile": profile.name,
        "jobs": store.status_counts(conn, profile.name),
        "searches_enabled": sum(s["enabled"] for s in searches),
        "searches_total": len(searches),
        "latest_runs": store.latest_runs(conn, profile.name),
    }


@router.get("/events", response_model=list[EventOut], summary="Activity log, newest first")
def events(
    profile: CurrentProfile, conn: Conn, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[dict]:
    return store.list_events(conn, profile.name, limit)
