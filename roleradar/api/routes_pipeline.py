"""Starting and stopping the long-running steps: rank (and, later, build)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from roleradar.api.deps import Conn, CurrentProfile, SettingsDep
from roleradar.api.schemas import Started, Stopped
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
