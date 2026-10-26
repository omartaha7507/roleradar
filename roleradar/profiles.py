"""Profiles: one folder per person who uses RoleRadar.

    profiles/<name>/
        profile.yaml   machine-readable rules: languages, years, locations, lanes
        identity.md    CV facts (contact, jobs, education), read when writing CVs
        projects.md    evidence per project, read when picking what to show

Every gate, bonus and language rule comes from the person's own profile.yaml,
never from one global config file. That is what lets a friend in Cairo and
someone in Berlin use the same program with completely different rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Folder names become part of file paths and URLs, so they are kept strict.
# This also blocks tricks like "../../Windows" in a URL.
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Language levels in order (CEFR). "native" sits above C2.
CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2", "native"]
CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native"]


def cefr_rank(level: str) -> int:
    """A1 -> 0 ... native -> 6, so levels can be compared with < and >."""
    return CEFR_LEVELS.index(level)


class Strict(BaseModel):
    # Unknown keys are an error: a typo like "drop_at_yaers" should fail loudly
    # instead of silently doing nothing.
    model_config = ConfigDict(extra="forbid")


class LocationTier(Strict):
    label: str
    match: list[str] = Field(min_length=1, description="Words matched in the job's location")
    bonus: int = Field(default=0, ge=-50, le=50)


class Locations(Strict):
    priority: list[LocationTier] = Field(default_factory=list)
    remote_ok: bool = True
    # Places the person cannot take (no work permit, too far, ...)
    exclude: list[str] = Field(default_factory=list)


class Experience(Strict):
    years: float = Field(ge=0, le=50)
    drop_at_years: int = Field(ge=1, le=50, description="Drop jobs that state this many years or more")


class Roles(Strict):
    target: list[str] = Field(min_length=1)
    out_of_lane: list[str] = Field(default_factory=list)
    seniority_words: list[str] = Field(
        default_factory=lambda: ["Staff", "Principal", "Lead", "Head of", "Director", "Architect"]
    )


class Scoring(Strict):
    min_fit: int = Field(default=45, ge=0, le=100)
    strong_score: int = Field(default=95, ge=0, le=200)


class ProfileConfig(Strict):
    display_name: str = Field(min_length=1, max_length=80)
    languages: dict[str, CefrLevel] = Field(min_length=1)
    experience: Experience
    roles: Roles
    locations: Locations = Field(default_factory=Locations)
    scoring: Scoring = Field(default_factory=Scoring)

    @field_validator("languages")
    @classmethod
    def _title_case_languages(cls, value: dict[str, str]) -> dict[str, str]:
        # "german" and "German" mean the same thing.
        return {name.strip().title(): level for name, level in value.items()}

    def level_in(self, language: str) -> int:
        """The person's level in a language as a number; -1 if they don't speak it."""
        level = self.languages.get(language.title())
        return cefr_rank(level) if level else -1


@dataclass
class Profile:
    name: str
    path: Path
    config: ProfileConfig
    identity_md: str
    projects_md: str


class ProfileError(Exception):
    """A profile folder exists but something in it is missing or invalid."""


class ProfileNotFound(LookupError):
    pass


def validate_name(name: str) -> str:
    if not PROFILE_NAME_RE.match(name):
        raise ProfileNotFound(name)
    return name


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_profile(profiles_dir: Path, name: str) -> Profile:
    folder = profiles_dir / validate_name(name)
    yaml_path = folder / "profile.yaml"
    if not yaml_path.exists():
        raise ProfileNotFound(name)
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"profile.yaml is not valid YAML: {exc}") from exc
    try:
        config = ProfileConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ProfileError(f"profile.yaml has problems: {problems}") from exc
    return Profile(
        name=name,
        path=folder,
        config=config,
        identity_md=_read_text(folder / "identity.md"),
        projects_md=_read_text(folder / "projects.md"),
    )


def list_profile_names(profiles_dir: Path) -> list[str]:
    if not profiles_dir.exists():
        return []
    return sorted(
        p.name
        for p in profiles_dir.iterdir()
        if p.is_dir() and PROFILE_NAME_RE.match(p.name) and (p / "profile.yaml").exists()
    )
