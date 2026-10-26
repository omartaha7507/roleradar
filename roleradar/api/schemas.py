"""The shapes of the JSON going in and out of the API.

FastAPI reads these classes to check every request automatically: a job
without a title, or a search with pages=7, is rejected with a clear message
before any of our code runs. The same classes produce the /docs page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

JobStatus = Literal["new", "passed", "dropped", "ranked", "built", "applied", "skipped", "closed"]
Source = Literal["linkedin", "indeed", "stepstone", "arbeitnow", "wuzzuf", "bayt", "manual", "other"]


def _check_http_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    return value


class JobIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=100_000)
    posted_at: str | None = Field(default=None, max_length=40)

    _url = field_validator("url")(_check_http_url)


class IngestRequest(BaseModel):
    source: Source
    search_id: int | None = None
    jobs: list[JobIn] = Field(min_length=1, max_length=500)


class IngestResponse(BaseModel):
    inserted: int
    updated: int
    reposts: int
    passed: int = Field(description="New jobs that passed the rule filter")
    dropped: int = Field(description="New jobs the rule filter dropped")


class FindingOut(BaseModel):
    rule: str
    message: str
    evidence: str


class FilterResultOut(BaseModel):
    decision: Literal["pass", "drop"]
    reasons: list[FindingOut]
    notes: list[FindingOut]


class FilterRunOut(BaseModel):
    run_id: int
    checked: int
    passed: int
    dropped: int
    reasons: dict[str, int] = Field(description="How many dropped jobs each rule caught")


class FilterTestIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(default="", max_length=200)
    location: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=100_000)


class JobSummary(BaseModel):
    id: int
    source: str
    url: str
    title: str
    company: str
    location: str | None
    posted_at: str | None
    status: JobStatus
    status_reason: str | None
    duplicate_of: int | None
    first_seen_at: str
    last_seen_at: str
    description_chars: int | None


class JobDetail(JobSummary):
    description: str | None
    search_id: int | None
    updated_at: str
    filter_result: FilterResultOut | None
    filtered_at: str | None


class JobPage(BaseModel):
    total: int
    items: list[JobSummary]


class SearchIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2000)
    stage: int = Field(default=1, ge=1, le=9, description="1 = first round, 2 = next, ...")
    pages: int = Field(default=1, ge=1, le=3, description="Result pages to read (about 25 jobs each)")
    enabled: bool = True

    _url = field_validator("url")(_check_http_url)


class SearchPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=2000)
    stage: int | None = Field(default=None, ge=1, le=9)
    pages: int | None = Field(default=None, ge=1, le=3)
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _check_http_url(value) if value is not None else None


class SearchOut(BaseModel):
    id: int
    label: str
    url: str
    site: str
    stage: int
    pages: int
    enabled: bool
    created_at: str


class SearchCreated(SearchOut):
    hints: list[str] = Field(description="Advice on the search URL, e.g. missing filters")
    suggested_url: str | None = Field(description="The same search with the suggested filters added")


class ProfileListItem(BaseModel):
    name: str
    display_name: str | None
    valid: bool
    error: str | None = None


class ProfileOut(BaseModel):
    name: str
    config: dict
    has_identity: bool
    has_projects: bool


class RunOut(BaseModel):
    id: int
    kind: str
    status: str
    total: int
    done: int
    message: str | None
    started_at: str
    finished_at: str | None


class Summary(BaseModel):
    profile: str
    jobs: dict[str, int]
    searches_enabled: int
    searches_total: int
    latest_runs: dict[str, RunOut]


class EventOut(BaseModel):
    id: int
    profile: str | None
    at: str
    level: str
    kind: str
    message: str
    data: dict | None


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    schema_version: int
    profiles: int
