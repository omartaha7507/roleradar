"""Scraping: the dashboard asks for a scrape, the Chrome extension does it."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from roleradar import scraping, store
from roleradar.api.deps import Conn, CurrentProfile
from roleradar.api.schemas import JobDetail

router = APIRouter(tags=["scraping"])


class ScrapeIn(BaseModel):
    stage: int | None = Field(default=None, ge=1, le=9, description="Only searches of this stage; empty = all")


class ProgressIn(BaseModel):
    done: int = Field(ge=0)
    message: str = Field(max_length=300)


class FinishIn(BaseModel):
    ok: bool
    message: str = Field(max_length=500)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(applied|skipped|ranked|built)$")


class AppliedIn(BaseModel):
    url: str = Field(max_length=2000, pattern="^https?://")
    title: str | None = Field(default=None, max_length=300)
    company: str | None = Field(default=None, max_length=200)


# --- Dashboard side -------------------------------------------------------------------------

@router.post("/api/profiles/{name}/scrape", status_code=202, summary="Ask the extension to scrape")
def request_scrape(body: ScrapeIn, profile: CurrentProfile, conn: Conn) -> dict:
    try:
        return scraping.request_scrape(conn, profile.name, body.stage)
    except scraping.ScrapeError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/api/profiles/{name}/scrape/cancel", summary="Cancel a waiting or running scrape")
def cancel_scrape(profile: CurrentProfile, conn: Conn) -> dict:
    return {"cancelled": scraping.cancel(conn, profile.name)}


@router.post("/api/profiles/{name}/jobs/{job_id}/status", response_model=JobDetail,
             summary="Mark a job applied or skipped (or undo)")
def set_status(job_id: int, body: StatusIn, profile: CurrentProfile, conn: Conn) -> dict:
    job = store.set_status(conn, profile.name, job_id, body.status)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/api/profiles/{name}/applied", response_model=JobDetail,
             summary="Mark the job at this URL applied (used by the extension)")
def applied_by_url(body: AppliedIn, profile: CurrentProfile, conn: Conn) -> dict:
    try:
        return store.mark_applied_by_url(conn, profile.name, body.url, body.title, body.company)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


# --- Extension side -------------------------------------------------------------------------

@router.get("/api/extension/next", summary="The extension checks in: anything to scrape?")
def next_request(conn: Conn) -> dict:
    return scraping.claim_next(conn) or {"request_id": None}


@router.post("/api/extension/requests/{request_id}/progress", summary="The extension reports progress")
def report_progress(request_id: int, body: ProgressIn, conn: Conn) -> dict:
    # "cancelled: true" tells the extension to stop after the current page.
    if scraping.is_cancelled(conn, request_id):
        return {"cancelled": True}
    try:
        scraping.report_progress(conn, request_id, body.done, body.message)
    except scraping.ScrapeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"cancelled": False}


@router.post("/api/extension/requests/{request_id}/finish", summary="The extension is done")
def report_finished(request_id: int, body: FinishIn, conn: Conn) -> dict:
    if scraping.is_cancelled(conn, request_id):
        return {"ok": True}
    try:
        scraping.report_finished(conn, request_id, body.ok, body.message)
    except scraping.ScrapeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True}
