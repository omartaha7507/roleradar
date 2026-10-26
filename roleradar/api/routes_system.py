"""Health check and the list of profiles."""

from __future__ import annotations

from fastapi import APIRouter

from roleradar import __version__
from roleradar.api.deps import Conn, CurrentProfile, SettingsDep
from roleradar.api.schemas import Health, ProfileListItem, ProfileOut
from roleradar.db import schema_version
from roleradar.profiles import ProfileError, list_profile_names, load_profile

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=Health, summary="Is the server up?")
def health(conn: Conn, settings: SettingsDep) -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "schema_version": schema_version(conn),
        "profiles": len(list_profile_names(settings.profiles_dir)),
    }


@router.get("/profiles", response_model=list[ProfileListItem], summary="All profiles, valid or not")
def profiles(settings: SettingsDep) -> list[dict]:
    items = []
    for name in list_profile_names(settings.profiles_dir):
        try:
            profile = load_profile(settings.profiles_dir, name)
            items.append({"name": name, "display_name": profile.config.display_name, "valid": True})
        except ProfileError as exc:
            # Listed anyway, so a broken profile shows up with its problem
            # instead of silently disappearing.
            items.append({"name": name, "display_name": None, "valid": False, "error": str(exc)})
    return items


@router.get("/profiles/{name}", response_model=ProfileOut, summary="One profile's rules")
def profile(profile: CurrentProfile) -> dict:
    return {
        "name": profile.name,
        "config": profile.config.model_dump(),
        "has_identity": bool(profile.identity_md.strip()),
        "has_projects": bool(profile.projects_md.strip()),
    }
