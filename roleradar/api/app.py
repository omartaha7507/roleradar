"""Builds the FastAPI app.

`create_app(settings)` instead of one global `app` object means the tests can
start a fresh app pointed at a temporary folder, without touching your data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from roleradar import __version__
from roleradar.api import routes_jobs, routes_pipeline, routes_scrape, routes_searches, routes_system
from roleradar.db import connect, log_event, migrate
from roleradar.ollama import OllamaClient
from roleradar.profiles import ProfileError, ProfileNotFound
from roleradar.runs import mark_interrupted
from roleradar.scraping import mark_interrupted_requests
from roleradar.settings import Settings, load_settings
from roleradar.workers import Workers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs once when the server starts: create or upgrade the database,
        # and close any run that a previous shutdown cut off.
        conn = connect(settings.db_path)
        try:
            version = migrate(conn)
            mark_interrupted(conn)
            mark_interrupted_requests(conn)
            log_event(conn, "server", f"Server started (database version {version})")
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="RoleRadar",
        version=__version__,
        description="Local API for the RoleRadar job pipeline. Runs only on this computer.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.workers = Workers()
    # Tests replace these factories with fakes, so no real AI is needed to test.
    app.state.make_ollama = lambda: OllamaClient(settings.ollama_url, settings.ollama_timeout)

    @app.exception_handler(ProfileNotFound)
    async def _profile_not_found(_: Request, exc: ProfileNotFound) -> JSONResponse:
        return JSONResponse({"detail": f"No profile named '{exc}'"}, status_code=404)

    @app.exception_handler(ProfileError)
    async def _profile_invalid(_: Request, exc: ProfileError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    app.include_router(routes_system.router)
    app.include_router(routes_jobs.router)
    app.include_router(routes_searches.router)
    app.include_router(routes_pipeline.router)
    app.include_router(routes_scrape.router)
    return app
