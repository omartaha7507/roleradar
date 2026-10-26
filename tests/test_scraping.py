"""The hand-over between dashboard and extension, as the extension sees it."""

from .conftest import make_job

SEARCH = "https://www.linkedin.com/jobs/search/?keywords=ai&geoId=106967730&f_TPR=r604800&f_E=2%2C3"


def add_search(client, **extra):
    return client.post("/api/profiles/alex/searches", json={"label": "AI Berlin", "url": SEARCH, **extra}).json()


def test_nothing_to_do(client):
    assert client.get("/api/extension/next").json() == {"request_id": None}
    health = client.get("/api/profiles/alex/summary").json()
    assert health["profile"] == "alex"


def test_scrape_needs_a_search(client):
    response = client.post("/api/profiles/alex/scrape", json={})
    assert response.status_code == 409 and "No enabled searches" in response.json()["detail"]


def test_full_scrape_round(client):
    search = add_search(client, pages=2)
    requested = client.post("/api/profiles/alex/scrape", json={}).json()
    assert requested["searches"] == 1
    assert client.post("/api/profiles/alex/scrape", json={}).status_code == 409  # one at a time

    task = client.get("/api/extension/next").json()
    assert task["profile"] == "alex" and task["searches"][0]["pages"] == 2
    assert client.get("/api/extension/next").json() == {"request_id": None}  # claimed once only

    rid = task["request_id"]
    client.post(f"/api/profiles/alex/jobs", json={"source": "linkedin", "search_id": search["id"], "jobs": [make_job(1)]})
    assert client.post(f"/api/extension/requests/{rid}/progress", json={"done": 1, "message": "AI Berlin"}).json() == {"cancelled": False}
    run = client.get("/api/profiles/alex/summary").json()["latest_runs"]["scrape"]
    assert (run["status"], run["done"], run["total"]) == ("running", 1, 1)

    client.post(f"/api/extension/requests/{rid}/finish", json={"ok": True, "message": "1 search, 1 job"})
    run = client.get("/api/profiles/alex/summary").json()["latest_runs"]["scrape"]
    assert run["status"] == "done" and run["message"] == "1 search, 1 job"


def test_cancel_reaches_the_extension(client):
    add_search(client)
    client.post("/api/profiles/alex/scrape", json={})
    rid = client.get("/api/extension/next").json()["request_id"]
    assert client.post("/api/profiles/alex/scrape/cancel").json() == {"cancelled": True}
    assert client.post(f"/api/extension/requests/{rid}/progress", json={"done": 0, "message": "x"}).json() == {"cancelled": True}
    assert client.post(f"/api/extension/requests/{rid}/finish", json={"ok": True, "message": "x"}).status_code == 200


def test_stage_filter(client):
    add_search(client, stage=2)
    assert client.post("/api/profiles/alex/scrape", json={"stage": 1}).status_code == 409
    assert client.post("/api/profiles/alex/scrape", json={"stage": 2}).json()["searches"] == 1


def test_mark_applied_known_and_unknown_jobs(client):
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1)]})
    known = client.post("/api/profiles/alex/applied",
                        json={"url": "https://www.linkedin.com/jobs/view/4000000001/?trk=x"}).json()
    assert known["status"] == "applied"

    unknown_url = "https://careers.example.com/jobs/77"
    assert client.post("/api/profiles/alex/applied", json={"url": unknown_url}).status_code == 422
    added = client.post("/api/profiles/alex/applied",
                        json={"url": unknown_url, "title": "ML Engineer", "company": "Example AG"}).json()
    assert added["status"] == "applied" and added["source"] == "other"

    # Later the same role shows up on LinkedIn: recognised as already applied.
    repost = make_job(9, title="ML Engineer (m/w/d)", company="Example")
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [repost]})
    dropped = client.get("/api/profiles/alex/jobs", params={"status": "dropped"}).json()["items"][0]
    assert dropped["status_reason"].startswith("Already applied")


def test_status_buttons(client):
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1)]})
    job_id = client.get("/api/profiles/alex/jobs").json()["items"][0]["id"]
    assert client.post(f"/api/profiles/alex/jobs/{job_id}/status", json={"status": "skipped"}).json()["status"] == "skipped"
    assert client.post(f"/api/profiles/alex/jobs/{job_id}/status", json={"status": "deleted"}).status_code == 422
    assert client.post("/api/profiles/alex/jobs/999/status", json={"status": "applied"}).status_code == 404
