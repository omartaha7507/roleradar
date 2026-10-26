from .conftest import make_job

SEARCH = "https://www.linkedin.com/jobs/search/?keywords=ai%20engineer&location=Berlin"


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["profiles"] == 1 and body["schema_version"] >= 1


def test_root_redirects_to_docs(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_profiles_list_and_detail(client):
    assert client.get("/api/profiles").json() == [
        {"name": "alex", "display_name": "Alex Example", "valid": True, "error": None}
    ]
    body = client.get("/api/profiles/alex").json()
    assert body["config"]["languages"]["German"] == "B1"
    assert body["has_identity"] and body["has_projects"]


def test_unknown_profile_is_404(client):
    assert client.get("/api/profiles/nobody/jobs").status_code == 404
    assert client.get("/api/profiles/..%2Fetc/jobs").status_code == 404


def test_broken_profile_is_listed_with_its_error(client, settings):
    broken = settings.profiles_dir / "broken"
    broken.mkdir()
    (broken / "profile.yaml").write_text("display_name: X\n")
    items = {p["name"]: p for p in client.get("/api/profiles").json()}
    assert items["broken"]["valid"] is False and "languages" in items["broken"]["error"]
    assert client.get("/api/profiles/broken/jobs").status_code == 422


def test_ingest_list_and_detail(client):
    response = client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1), make_job(2)]})
    assert response.json() == {"inserted": 2, "updated": 0, "reposts": 0, "passed": 2, "dropped": 0}

    page = client.get("/api/profiles/alex/jobs", params={"status": "passed"}).json()
    assert page["total"] == 2 and page["items"][0]["title"] == "Junior AI Engineer 2"

    job_id = page["items"][0]["id"]
    detail = client.get(f"/api/profiles/alex/jobs/{job_id}").json()
    assert detail["description"] == "We build things with Python."
    assert detail["filter_result"]["decision"] == "pass"
    assert client.get("/api/profiles/alex/jobs/9999").status_code == 404


def test_ingest_rejects_bad_jobs(client):
    for bad in ({**make_job(), "title": ""}, {**make_job(), "url": "javascript:alert(1)"}):
        response = client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [bad]})
        assert response.status_code == 422
    assert client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": []}).status_code == 422
    assert client.post("/api/profiles/alex/jobs", json={"source": "myspace", "jobs": [make_job()]}).status_code == 422


def test_ingest_with_unknown_search_is_404(client):
    response = client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "search_id": 99, "jobs": [make_job()]})
    assert response.status_code == 404


def test_search_crud_and_advice(client):
    created = client.post("/api/profiles/alex/searches", json={"label": "AI Berlin", "url": SEARCH})
    assert created.status_code == 201
    body = created.json()
    assert body["site"] == "linkedin" and body["pages"] == 1 and body["enabled"] is True
    assert len(body["hints"]) == 3  # no date filter, no experience filter, no geoId
    assert "f_TPR=r604800" in body["suggested_url"] and "f_E=2,3" in body["suggested_url"]

    assert client.post("/api/profiles/alex/searches", json={"label": "Again", "url": SEARCH}).status_code == 409

    sid = body["id"]
    patched = client.patch(f"/api/profiles/alex/searches/{sid}", json={"enabled": False, "stage": 2})
    assert patched.json()["enabled"] is False and patched.json()["stage"] == 2
    assert client.patch(f"/api/profiles/alex/searches/{sid}", json={"pages": 7}).status_code == 422

    assert client.delete(f"/api/profiles/alex/searches/{sid}").status_code == 204
    assert client.delete(f"/api/profiles/alex/searches/{sid}").status_code == 404


def test_good_search_gets_no_advice(client):
    url = SEARCH + "&geoId=106967730&f_TPR=r604800&f_E=2%2C3"
    body = client.post("/api/profiles/alex/searches", json={"label": "Good", "url": url}).json()
    assert body["hints"] == [] and body["suggested_url"] is None


def test_summary_and_events(client):
    client.post("/api/profiles/alex/searches", json={"label": "AI Berlin", "url": SEARCH})
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1)]})
    summary = client.get("/api/profiles/alex/summary").json()
    assert summary["jobs"]["passed"] == 1 and summary["searches_enabled"] == 1
    assert summary["latest_runs"]["filter"]["status"] == "done"
    kinds = [e["kind"] for e in client.get("/api/profiles/alex/events").json()]
    assert kinds[:3] == ["filter", "ingest", "search"] and "server" in kinds


def test_docs_page_exists(client):
    assert client.get("/docs").status_code == 200
    assert "/api/profiles/{name}/jobs" in client.get("/openapi.json").json()["paths"]


def test_filter_test_endpoint_saves_nothing(client):
    body = {"title": "Head of AI", "description": "Fluent German (C1) required. 8+ years of experience."}
    result = client.post("/api/profiles/alex/filter/test", json=body).json()
    assert result["decision"] == "drop"
    assert {r["rule"] for r in result["reasons"]} == {"years", "language", "seniority"}
    assert client.get("/api/profiles/alex/jobs").json()["total"] == 0


def test_ingest_filters_and_rerun_endpoint(client):
    senior = make_job(2, title="Staff ML Engineer", description="Own the platform.")
    body = client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1), senior]}).json()
    assert (body["passed"], body["dropped"]) == (1, 1)

    dropped = client.get("/api/profiles/alex/jobs", params={"status": "dropped"}).json()["items"][0]
    assert dropped["status_reason"].startswith("'Staff' in the title")

    rerun = client.post("/api/profiles/alex/filter", params={"rerun": True}).json()
    assert (rerun["checked"], rerun["passed"], rerun["dropped"]) == (2, 1, 1)
    assert rerun["reasons"] == {"seniority": 1}
