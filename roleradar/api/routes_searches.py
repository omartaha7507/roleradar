"""Saved searches: the URLs the extension opens when a scrape runs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from roleradar import store
from roleradar.api.deps import Conn, CurrentProfile
from roleradar.api.schemas import SearchCreated, SearchIn, SearchOut, SearchPatch
from roleradar.searches import linkedin_advice

router = APIRouter(prefix="/api/profiles/{name}/searches", tags=["searches"])


@router.get("", response_model=list[SearchOut], summary="All saved searches, by stage")
def list_searches(profile: CurrentProfile, conn: Conn) -> list[dict]:
    return store.list_searches(conn, profile.name)


@router.post("", response_model=SearchCreated, status_code=201, summary="Save a search")
def create_search(body: SearchIn, profile: CurrentProfile, conn: Conn) -> dict:
    try:
        created = store.create_search(conn, profile.name, body.model_dump())
    except store.DuplicateSearch:
        raise HTTPException(409, "This search URL is already saved") from None
    hints, suggested = linkedin_advice(created["url"])
    return {**created, "hints": hints, "suggested_url": suggested}


@router.patch("/{search_id}", response_model=SearchOut, summary="Change a search")
def update_search(search_id: int, body: SearchPatch, profile: CurrentProfile, conn: Conn) -> dict:
    try:
        updated = store.update_search(conn, profile.name, search_id, body.model_dump(exclude_none=True))
    except store.DuplicateSearch:
        raise HTTPException(409, "Another saved search already has this URL") from None
    if not updated:
        raise HTTPException(404, "Search not found")
    return updated


@router.delete("/{search_id}", status_code=204, summary="Delete a search")
def delete_search(search_id: int, profile: CurrentProfile, conn: Conn) -> Response:
    if not store.delete_search(conn, profile.name, search_id):
        raise HTTPException(404, "Search not found")
    return Response(status_code=204)
