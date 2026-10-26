"""The CV builder, tested with a fake Claude.

The fake can be honest, can invent an employer (the fact check must catch
it), or can return broken JSON (the retry must handle it).
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from roleradar import store
from roleradar.builder import BuildError, FatalBuildError, claude_runner, parse_application, run_build, slug
from roleradar.filtering import run_filter
from roleradar.profiles import load_profile
from roleradar.runs import start_run
from roleradar.settings import Settings

from .conftest import make_job

HEADLESS = "/opt/pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell"

HONEST = {
    "language": "en",
    "cv": {
        "contact": {"name": "Alex Example", "email": "alex@example.com", "city": "Berlin, Germany"},
        "headline": "Junior AI Engineer — Python and RAG",
        "summary": "Builds end-to-end ML systems.",
        "skills": [{"label": "ML", "items": ["PyTorch", "scikit-learn"]}],
        "experience": [{"role": "Junior Data Analyst", "company": "Example GmbH", "dates": "Mar 2025 - Sep 2025",
                        "bullets": ["Built a Python pipeline that cleaned 40k sales records a week"]}],
        "projects": [{"name": "RAG Study Assistant", "bullets": ["Answers questions over 300 lecture PDFs"]}],
        "education": [{"degree": "B.Sc. Computer Engineering", "school": "Example University", "dates": "2021 - 2025"}],
        "languages": ["English (C1)", "German (B1)"],
    },
    "letter": {"greeting": "Dear Acme team,", "paragraphs": ["One.", "Two."], "closing": "Kind regards,"},
    "fit": {"summary": "Good match.", "flags": ["Asks for Kubernetes"]},
}
INVENTED = json.loads(json.dumps(HONEST))
INVENTED["cv"]["experience"][0]["company"] = "Google DeepMind"


class FakeClaude:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt: str, cwd: Path) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer if isinstance(answer, str) else "Here you go:\n```json\n" + json.dumps(answer) + "\n```"


def always_open(url):
    return "open", "The posting is online"


@pytest.fixture
def ranked(conn, settings):
    """Two jobs, filtered and marked ranked, ready to build."""
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, company="Acme GmbH"), make_job(2, company="Beta AG")])
    run_filter(conn, load_profile(settings.profiles_dir, "alex"))
    conn.execute("UPDATE jobs SET status = 'ranked', rank_score = 90 - id")
    return conn


def build(settings, conn, fake, **kwargs):
    run_id = start_run(conn, "alex", "build", 0)
    run_build(settings, "alex", threading.Event(), run_id=run_id, runner=fake,
              check_live=kwargs.pop("check_live", always_open), **kwargs)
    return store.latest_runs(conn, "alex")["build"]


def test_parse_strips_dashes_and_fences():
    app = parse_application("Sure!\n```json\n" + json.dumps(HONEST) + "\n```")
    assert "—" not in app.cv.headline and app.cv.headline == "Junior AI Engineer, Python and RAG"


def test_build_writes_files_and_marks_built(ranked, settings, monkeypatch):
    monkeypatch.setenv("ROLERADAR_CHROME", HEADLESS if Path(HEADLESS).exists() else "/nonexistent")
    fake = FakeClaude(HONEST)
    run = build(settings, ranked, fake, count=1)
    assert run["status"] == "done" and run["message"].startswith("Built 1 application")

    built = store.list_jobs(ranked, "alex", status="built")[0]
    assert [j["company"] for j in built] == ["Acme GmbH"]  # the best-scored job first
    row = ranked.execute("SELECT * FROM builds").fetchone()
    folder = Path(row["folder"])
    assert (folder / "application.json").exists() and (folder / "job.md").exists()
    assert (folder / "Alex-Example_CV_Acme-GmbH.html").exists()
    if Path(HEADLESS).exists():
        assert row["cv_pdf"] == "Alex-Example_CV_Acme-GmbH.pdf" and (folder / row["cv_pdf"]).stat().st_size > 1000
    assert json.loads(row["flags"]) == ["Asks for Kubernetes"]
    prompt = fake.prompts[0]
    assert "Example GmbH" in prompt and "RAG Study Assistant" in prompt and "Write in English" in prompt


def test_invented_employer_is_caught_and_retried(ranked, settings):
    fake = FakeClaude(INVENTED, HONEST)
    assert build(settings, ranked, fake, count=1)["message"].startswith("Built 1")
    assert "Employer 'Google DeepMind' is not in identity.md" in fake.prompts[1]


def test_two_bad_answers_fail_that_job_only(ranked, settings):
    run = build(settings, ranked, FakeClaude(INVENTED), count=2)
    assert run["status"] == "done" and "2 failed" in run["message"]
    assert store.status_counts(ranked, "alex")["ranked"] == 2  # still waiting, nothing half-built
    warning = store.list_events(ranked, "alex")[1]
    assert warning["level"] == "warning" and "Google DeepMind" in warning["message"]


def test_broken_json_then_good(ranked, settings):
    fake = FakeClaude("I could not do that", HONEST)
    assert build(settings, ranked, fake, count=1)["message"].startswith("Built 1")
    assert "contained no JSON" in fake.prompts[1]


def test_closed_postings_are_skipped(ranked, settings):
    fake = FakeClaude(HONEST)
    run = build(settings, ranked, fake, count=2,
                check_live=lambda url: ("closed", "The posting says it no longer accepts applications"))
    assert "2 postings had closed" in run["message"] and fake.prompts == []
    assert store.status_counts(ranked, "alex")["closed"] == 2


def test_pick_exact_jobs(ranked, settings):
    second = store.list_jobs(ranked, "alex", status="ranked", sort="score")[0][1]
    build(settings, ranked, FakeClaude(HONEST), job_ids=[second["id"]])
    assert [j["id"] for j in store.list_jobs(ranked, "alex", status="built")[0]] == [second["id"]]


def test_fatal_error_stops_the_run(ranked, settings):
    def signed_out(prompt, cwd):
        raise FatalBuildError("Claude Code is not signed in")
    run = build(settings, ranked, signed_out, count=2)
    assert run["status"] == "failed" and "not signed in" in run["message"]


def test_slug():
    assert slug("Junior AI Engineer (m/w/d)") == "Junior-AI-Engineer"
    assert slug("Müller & Söhne GmbH") == "Muller-Sohne-GmbH"


# --- The real subprocess runner, with a pretend `claude` program ----------------------------------

FAKE_CLI = '''#!{python}
import json, sys
args = sys.argv[1:]
assert args[:3] == ["-p", "--output-format", "json"], args
prompt = sys.stdin.read()
mode = "{mode}"
if mode == "signed_out":
    print(json.dumps({{"type": "result", "is_error": True, "result": "Invalid API key. Please run /login"}}))
else:
    print(json.dumps({{"type": "result", "is_error": False, "result": json.dumps({answer}), "total_cost_usd": 0.01}}))
'''


def fake_cli(tmp_path: Path, mode: str) -> Settings:
    script = tmp_path / f"claude-{mode}"
    script.write_text(FAKE_CLI.format(python=sys.executable, mode=mode, answer=json.dumps(HONEST)))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return Settings(data_dir=tmp_path, profiles_dir=tmp_path, claude_command=str(script))


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script")
def test_real_runner_parses_claude_output(tmp_path):
    run = claude_runner(fake_cli(tmp_path, "ok"))
    assert parse_application(run("prompt", tmp_path)).cv.contact.name == "Alex Example"


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script")
def test_real_runner_reports_sign_in(tmp_path):
    run = claude_runner(fake_cli(tmp_path, "signed_out"))
    with pytest.raises(FatalBuildError, match="not signed in"):
        run("prompt", tmp_path)


def test_missing_claude_is_explained(tmp_path):
    with pytest.raises(BuildError, match="Claude Code was not found"):
        claude_runner(Settings(data_dir=tmp_path, profiles_dir=tmp_path, claude_command="no-such-claude-xyz"))


# --- Endpoints ----------------------------------------------------------------------------------------------

def test_build_endpoint_and_files(client, monkeypatch):
    monkeypatch.setenv("ROLERADAR_CHROME", "/nonexistent")  # HTML only: fast
    client.app.state.make_claude = lambda: FakeClaude(HONEST)
    client.app.state.live_check = always_open
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1)]})
    job_id = client.get("/api/profiles/alex/jobs").json()["items"][0]["id"]

    started = client.post("/api/profiles/alex/build", json={"job_ids": [job_id]})
    assert started.status_code == 202 and started.json()["total"] == 1
    client.app.state.workers.wait("alex", "build", timeout=20)

    builds = client.get("/api/profiles/alex/builds").json()
    assert builds[0]["title"] == "Junior AI Engineer 1" and "folder" not in builds[0]
    bid = builds[0]["id"]
    html = client.get(f"/api/profiles/alex/builds/{bid}/cv.html")
    assert html.status_code == 200 and "Alex Example" in html.text
    assert client.get(f"/api/profiles/alex/builds/{bid}/cv.pdf").status_code == 404  # no browser here
    assert client.get(f"/api/profiles/alex/builds/{bid}/secrets.txt").status_code == 404


def test_build_endpoint_explains_missing_claude(client):
    def missing():
        raise BuildError("Claude Code was not found.")
    client.app.state.make_claude = missing
    response = client.post("/api/profiles/alex/build", json={})
    assert response.status_code == 503 and "not found" in response.json()["detail"]
