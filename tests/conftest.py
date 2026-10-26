from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from roleradar.api.app import create_app
from roleradar.db import connect, migrate
from roleradar.settings import PROJECT_ROOT, Settings

EXAMPLE = PROJECT_ROOT / "profiles" / "example"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A throwaway data folder with a copy of the example profile as 'alex'."""
    profiles = tmp_path / "profiles"
    shutil.copytree(EXAMPLE, profiles / "alex")
    return Settings(data_dir=tmp_path / "data", profiles_dir=profiles)


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:  # "with" runs startup
        yield test_client


@pytest.fixture
def conn(settings: Settings):
    connection = connect(settings.db_path)
    migrate(connection)
    yield connection
    connection.close()


def make_job(n: int = 1, **overrides) -> dict:
    job = {
        "url": f"https://www.linkedin.com/jobs/view/40000000{n:02d}/",
        "title": f"Junior AI Engineer {n}",
        "company": "Acme GmbH",
        "location": "Berlin, Germany",
        "description": "We build things with Python.",
    }
    job.update(overrides)
    return job
