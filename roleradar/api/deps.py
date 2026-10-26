"""Dependencies: small functions FastAPI runs before a route to hand it what it needs.

A route that declares `conn: Conn` gets a fresh database connection, closed
again after the response. A route that declares `profile: CurrentProfile`
gets the loaded profile for the {name} in the URL, or the caller gets a
404 / 422 without the route ever running.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from roleradar.db import connect
from roleradar.profiles import Profile, load_profile
from roleradar.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    # One connection per request. SQLite connections must not be shared between
    # threads, and FastAPI runs normal (non-async) routes in a thread pool.
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_profile(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> Profile:
    return load_profile(settings.profiles_dir, name)


SettingsDep = Annotated[Settings, Depends(get_settings)]
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
CurrentProfile = Annotated[Profile, Depends(get_profile)]
