"""The whole pipeline through the API, the way the extension and dashboard use it:
save a search, scrape, filter, rank, write, review, apply, and catch a re-post.
The AI and job sites are fakes; everything else is the real code.
"""

import subprocess
import sys

from roleradar.settings import read_settings_file

from .test_builder import HONEST, FakeClaude
from .test_ranker import FakeOllama

SEARCH = "https://www.linkedin.com/jobs/search/?keywords=ai%20engineer&location=Berlin"

SCRAPED = [
    {"url": "https://www.linkedin.com/jobs/view/4011111111/", "title": "Junior AI Engineer (m/w/d)",
     "company": "Acme GmbH", "location": "Berlin, Germany",
     "description": "You have 1 year of experience with Python and PyTorch. German is a plus."},
    {"url": "https://www.linkedin.com/jobs/view/4022222222/", "title": "ML Engineer",
     "company": "Beta AG", "location": "Munich, Germany",
     "description": "You bring 5+ years of experience in machine learning."},
    {"url": "https://www.linkedin.com/jobs/view/4033333333/", "title": "Data Plumber",
     "company": "Gamma SE", "location": "Berlin, Germany", "description": "Fix pipes. Python is a plus."},
]


def test_from_search_to_applied(client, monkeypatch):
    monkeypatch.setenv("ROLERADAR_CHROME", "/nonexistent")
    client.app.state.make_ollama = lambda: FakeOllama({"Data Plumber": 15})
    client.app.state.make_claude = lambda: FakeClaude(HONEST)
    client.app.state.live_check = lambda url: ("open", "")
    base = "/api/profiles/alex"

    # 1. Save a search: RoleRadar suggests the missing filters, and we take them.
    saved = client.post(f"{base}/searches", json={"label": "AI Berlin", "url": SEARCH}).json()
    client.patch(f"{base}/searches/{saved['id']}", json={"url": saved["suggested_url"]})

    # 2. Scrape: the dashboard asks, the extension picks it up and sends jobs.
    client.post(f"{base}/scrape", json={})
    task = client.get("/api/extension/next").json()
    ingest = client.post(f"{base}/jobs", json={"source": "linkedin", "search_id": task["searches"][0]["id"],
                                                "jobs": SCRAPED}).json()
    assert (ingest["passed"], ingest["dropped"]) == (2, 1)  # 5+ years dropped by the rules
    client.post(f"/api/extension/requests/{task['request_id']}/finish", json={"ok": True, "message": "done"})

    # 3. Rank: the weak fit is dropped, the good one ranked.
    client.post(f"{base}/rank")
    client.app.state.workers.wait("alex", "rank", timeout=20)
    summary = client.get(f"{base}/summary").json()
    assert summary["jobs"]["ranked"] == 1 and summary["jobs"]["dropped"] == 2

    # 4. Write the best one.
    client.post(f"{base}/build", json={"count": 3})
    client.app.state.workers.wait("alex", "build", timeout=20)
    builds = client.get(f"{base}/builds").json()
    assert [b["company"] for b in builds] == ["Acme GmbH"]
    assert "Alex Example" in client.get(f"{base}/builds/{builds[0]['id']}/cv.html").text

    # 5. Apply.
    client.post(f"{base}/jobs/{builds[0]['job_id']}/status", json={"status": "applied"})

    # 6. A month later Acme re-posts the role under a new ad: recognised.
    repost = {**SCRAPED[0], "url": "https://www.linkedin.com/jobs/view/4099999999/",
              "title": "Junior AI Engineer (f/m/x)", "company": "ACME"}
    assert client.post(f"{base}/jobs", json={"source": "linkedin", "jobs": [repost]}).json()["reposts"] == 1
    newest = client.get(f"{base}/jobs").json()["items"][0]
    assert newest["status"] == "dropped" and newest["status_reason"].startswith("Already applied")

    runs = client.get(f"{base}/summary").json()["latest_runs"]
    assert {kind: run["status"] for kind, run in runs.items()} == {
        "scrape": "done", "filter": "done", "rank": "done", "build": "done"}


def test_settings_file(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("# comment\nROLERADAR_OLLAMA_MODEL = llama3.2\nROLERADAR_PORT='8800'\nbroken line\n")
    assert read_settings_file(path) == {"ROLERADAR_OLLAMA_MODEL": "llama3.2", "ROLERADAR_PORT": "8800"}


def test_new_profile_command(tmp_path, settings):
    env = {"ROLERADAR_PROFILES_DIR": str(tmp_path), "ROLERADAR_DATA_DIR": str(tmp_path / "data"), "PATH": ""}
    (tmp_path / "example").mkdir()
    for f in (settings.profiles_dir / "alex").iterdir():
        (tmp_path / "example" / f.name).write_text(f.read_text())
    run = lambda *args: subprocess.run([sys.executable, "-m", "roleradar", *args], env=env,
                                       capture_output=True, text=True)
    assert run("new-profile", "sara-k").returncode == 0
    assert "display_name: Sara K" in (tmp_path / "sara-k" / "profile.yaml").read_text()
    assert "already exists" in run("new-profile", "sara-k").stderr
    assert "lowercase" in run("new-profile", "Sara K").stderr
