"""The ranker, tested with a fake model so no Ollama is needed.

The fake answers by title, counts how often it was asked (to prove the cache
works), and can be told to fail (to prove failures are handled).
"""

import threading

import pytest

from roleradar import store
from roleradar.filtering import run_filter
from roleradar.ollama import OllamaError
from roleradar.profiles import load_profile
from roleradar.ranker import Judgement, requirement_excerpt, run_rank, score

from .conftest import make_job


class FakeOllama:
    def __init__(self, fits=None, fail=False):
        self.fits = fits or {}
        self.fail = fail
        self.calls = 0

    def has_model(self, model):
        return True

    def chat_json(self, model, system, user, schema):
        self.calls += 1
        if self.fail:
            raise OllamaError("Ollama took longer than 300 seconds")
        title = user.split("Title: ")[1].split("\n")[0]
        fit = self.fits.get(title, 70)
        return {"fit": fit, "role_family": "AI Engineer", "junior_friendly": fit >= 50,
                "matches": ["Python"], "gaps": ["Kubernetes"], "reason": f"Fit for {title}"}


def prepare(conn, settings, jobs):
    store.ingest_jobs(conn, "alex", "linkedin", jobs)
    run_filter(conn, load_profile(settings.profiles_dir, "alex"))


def test_excerpt_keeps_requirements_and_drops_fluff():
    fluff = "Our office has a rooftop terrace and free fruit. " * 80
    needs = "You have 1 year of experience with Python. Knowledge of PyTorch is required. " * 5
    excerpt = requirement_excerpt(fluff + needs)
    assert "PyTorch" in excerpt and "rooftop" not in excerpt
    assert requirement_excerpt("Short ad. Rooftop terrace.") == "Short ad. Rooftop terrace."


def test_score_adds_up():
    from roleradar.profiles import ProfileConfig

    config = ProfileConfig.model_validate({
        "display_name": "T", "languages": {"English": "C1"},
        "experience": {"years": 1, "drop_at_years": 3},
        "roles": {"target": ["AI Engineer"]},
        "locations": {"priority": [{"label": "Home", "match": ["Berlin"], "bonus": 12}]},
    })
    good = Judgement(fit=70, role_family="AI", junior_friendly=True, matches=[], gaps=[], reason="ok")
    s = score(good, config, "Junior AI Engineer (m/w/d)", "Berlin, Germany")
    assert (s.total, s.location_bonus, s.title_bonus, s.junior_penalty) == (90, 12, 8, 0)

    far = good.model_copy(update={"junior_friendly": False})
    s = score(far, config, "Data Analyst", "Munich")
    assert (s.total, s.location_bonus, s.title_bonus, s.junior_penalty) == (60, 0, 0, 10)


def test_run_rank_keeps_good_and_drops_weak(conn, settings):
    prepare(conn, settings, [make_job(1, title="Junior AI Engineer"), make_job(2, title="Data Plumber")])
    fake = FakeOllama({"Data Plumber": 20})
    run_rank(settings, "alex", threading.Event(), client=fake)

    ranked = store.list_jobs(conn, "alex", status="ranked")[0]
    dropped = store.list_jobs(conn, "alex", status="dropped")[0]
    assert [j["title"] for j in ranked] == ["Junior AI Engineer"]
    assert ranked[0]["rank_score"] == 70 + 12 + 8  # fit + Berlin + title match
    assert dropped[0]["status_reason"].startswith("Weak fit (20/100)")

    run = store.latest_runs(conn, "alex")["rank"]
    assert run["status"] == "done" and run["done"] == run["total"] == 2


def test_cache_means_the_model_is_asked_once(conn, settings):
    prepare(conn, settings, [make_job(1)])
    fake = FakeOllama()
    run_rank(settings, "alex", threading.Event(), client=fake)
    run_rank(settings, "alex", threading.Event(), client=fake, rerank=True)
    assert fake.calls == 1
    assert "1 from cache" in store.latest_runs(conn, "alex")["rank"]["message"]


def test_changed_profile_asks_again(conn, settings):
    prepare(conn, settings, [make_job(1)])
    fake = FakeOllama()
    run_rank(settings, "alex", threading.Event(), client=fake)
    path = settings.profiles_dir / "alex" / "identity.md"
    path.write_text(path.read_text().replace("PyTorch", "PyTorch, JAX"))
    run_rank(settings, "alex", threading.Event(), client=fake, rerank=True)
    assert fake.calls == 2


def test_stop_is_respected(conn, settings):
    prepare(conn, settings, [make_job(1), make_job(2), make_job(3)])
    stop = threading.Event()
    stop.set()
    run_rank(settings, "alex", stop, client=FakeOllama())
    run = store.latest_runs(conn, "alex")["rank"]
    assert run["status"] == "stopped" and store.status_counts(conn, "alex")["passed"] == 3


def test_failures_leave_jobs_waiting_and_fail_the_run(conn, settings):
    prepare(conn, settings, [make_job(n) for n in range(1, 5)])
    run_rank(settings, "alex", threading.Event(), client=FakeOllama(fail=True))
    run = store.latest_runs(conn, "alex")["rank"]
    assert run["status"] == "failed" and "Three jobs in a row" in run["message"]
    assert store.status_counts(conn, "alex")["passed"] == 4  # nothing lost; rank again later


@pytest.fixture
def fake_client(client):
    fake = FakeOllama()
    client.app.state.make_ollama = lambda: fake
    return fake


def test_rank_endpoint_runs_in_background(client, fake_client):
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1), make_job(2)]})
    started = client.post("/api/profiles/alex/rank")
    assert started.status_code == 202 and started.json()["total"] == 2
    client.app.state.workers.wait("alex", "rank", timeout=10)

    page = client.get("/api/profiles/alex/jobs", params={"status": "ranked", "sort": "score"}).json()
    assert page["total"] == 2
    detail = client.get(f"/api/profiles/alex/jobs/{page['items'][0]['id']}").json()
    assert detail["rank_result"]["judgement"]["matches"] == ["Python"]


def test_rank_endpoint_explains_missing_ollama(client):
    class Down:
        def has_model(self, model):
            raise OllamaError("Ollama is not running. Open the Ollama app (or run 'ollama serve') and try again.")
    client.app.state.make_ollama = lambda: Down()
    client.post("/api/profiles/alex/jobs", json={"source": "linkedin", "jobs": [make_job(1)]})
    response = client.post("/api/profiles/alex/rank")
    assert response.status_code == 503 and "Ollama is not running" in response.json()["detail"]


def test_rank_endpoint_with_nothing_waiting(client, fake_client):
    assert client.post("/api/profiles/alex/rank").json()["total"] == 0
