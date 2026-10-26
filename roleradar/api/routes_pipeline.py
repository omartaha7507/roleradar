"""Starting and stopping the long-running steps: rank and build."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from roleradar.api.deps import Conn, CurrentProfile, SettingsDep
from roleradar.api.schemas import Started, Stopped
from roleradar.builder import BuildError, jobs_to_build, list_builds, run_build
from roleradar.ollama import OllamaError
from roleradar.ranker import jobs_to_rank, run_rank
from roleradar.runs import start_run
from roleradar.workers import AlreadyRunning

router = APIRouter(prefix="/api/profiles/{name}", tags=["pipeline"])


@router.post("/rank", response_model=Started, status_code=202, summary="Rank waiting jobs with the local AI")
def start_rank(
    request: Request,
    profile: CurrentProfile,
    conn: Conn,
    settings: SettingsDep,
    rerank: Annotated[bool, Query(description="Also re-judge jobs ranked before")] = False,
) -> dict:
    workers = request.app.state.workers
    if workers.is_running(profile.name, "rank"):
        raise HTTPException(409, "Ranking is already running for this profile")

    # Check Ollama before starting, so a problem is reported now, in plain words,
    # instead of as a failed run a minute later.
    client = request.app.state.make_ollama()
    try:
        if not client.has_model(settings.ollama_model):
            raise HTTPException(
                503, f"The model {settings.ollama_model} is not downloaded. "
                     f"Run: ollama pull {settings.ollama_model}")
    except OllamaError as exc:
        raise HTTPException(503, str(exc)) from None

    total = len(jobs_to_rank(conn, profile.name, rerank))
    if total == 0:
        return {"run_id": None, "total": 0, "message": "No jobs are waiting to be ranked"}
    run_id = start_run(conn, profile.name, "rank", total, f"Ranking with {settings.ollama_model}")
    try:
        workers.start(profile.name, "rank",
                      lambda stop: run_rank(settings, profile.name, stop, rerank=rerank, client=client, run_id=run_id))
    except AlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from None
    return {"run_id": run_id, "total": total, "message": f"Ranking {total} jobs"}


@router.post("/rank/stop", response_model=Stopped, summary="Stop ranking after the current job")
def stop_rank(request: Request, profile: CurrentProfile) -> dict:
    return {"stopping": request.app.state.workers.stop(profile.name, "rank")}


# --- Build ----------------------------------------------------------------------------------


class BuildIn(BaseModel):
    job_ids: list[int] | None = Field(default=None, max_length=50, description="Exactly these jobs")
    count: int | None = Field(default=5, ge=1, le=30, description="Otherwise: the best N ranked jobs")


@router.post("/build", response_model=Started, status_code=202, summary="Write CVs and cover letters")
def start_build(body: BuildIn, request: Request, profile: CurrentProfile, conn: Conn, settings: SettingsDep) -> dict:
    workers = request.app.state.workers
    if workers.is_running(profile.name, "build"):
        raise HTTPException(409, "A build is already running for this profile")
    try:
        runner = request.app.state.make_claude()
    except BuildError as exc:
        raise HTTPException(503, str(exc)) from None
    total = len(jobs_to_build(conn, profile.name, body.job_ids, body.count))
    if total == 0:
        return {"run_id": None, "total": 0, "message": "No ranked jobs to build. Rank first, or pick jobs."}
    run_id = start_run(conn, profile.name, "build", total, "Writing applications with Claude")
    check_live = request.app.state.live_check
    workers.start(profile.name, "build", lambda stop: run_build(
        settings, profile.name, stop, run_id=run_id, job_ids=body.job_ids, count=body.count,
        runner=runner, check_live=check_live))
    return {"run_id": run_id, "total": total, "message": f"Writing {total} applications"}


@router.post("/build/stop", response_model=Stopped, summary="Stop building after the current job")
def stop_build(request: Request, profile: CurrentProfile) -> dict:
    return {"stopping": request.app.state.workers.stop(profile.name, "build")}


@router.get("/builds", summary="Finished applications, newest first")
def builds(profile: CurrentProfile, conn: Conn) -> list[dict]:
    items = list_builds(conn, profile.name)
    for item in items:
        item.pop("folder", None)  # the dashboard gets links, not paths on disk
    return items


@router.get("/builds/{build_id}/{kind}", summary="Open the CV or cover letter (pdf or html)")
def build_file(build_id: int, kind: str, profile: CurrentProfile, conn: Conn) -> FileResponse:
    if kind not in {"cv.pdf", "letter.pdf", "cv.html", "letter.html"}:
        raise HTTPException(404, "Unknown file")
    row = conn.execute("SELECT * FROM builds WHERE id = ? AND profile = ?", (build_id, profile.name)).fetchone()
    if not row:
        raise HTTPException(404, "Build not found")
    folder = Path(row["folder"])
    which, ext = kind.split(".")
    name = row[f"{which}_pdf"] if ext == "pdf" else None
    if ext == "html":
        matches = sorted(folder.glob("*_CV_*.html" if which == "cv" else "*_Cover_Letter_*.html"))
        name = matches[0].name if matches else None
    if not name or not (folder / name).is_file():
        raise HTTPException(404, "This file was not created (no PDF without Chrome or Edge); try the html version")
    media = "application/pdf" if ext == "pdf" else "text/html"
    return FileResponse(folder / name, media_type=media, filename=name,
                        content_disposition_type="inline")
