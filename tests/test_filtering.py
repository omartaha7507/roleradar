from roleradar import store
from roleradar.filtering import run_filter
from roleradar.profiles import load_profile

from .conftest import make_job

SENIOR = make_job(2, title="Head of AI", description="10+ years of experience.")


def test_run_filter_updates_status_and_progress(conn, settings):
    profile = load_profile(settings.profiles_dir, "alex")
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1), SENIOR])

    summary = run_filter(conn, profile)
    assert (summary.checked, summary.passed, summary.dropped) == (2, 1, 1)
    assert summary.reasons == {"years": 1, "seniority": 1}

    run = conn.execute("SELECT * FROM runs WHERE id = ?", (summary.run_id,)).fetchone()
    assert (run["status"], run["done"], run["total"]) == ("done", 2, 2)

    dropped = store.list_jobs(conn, "alex", status="dropped")[0][0]
    assert dropped["status_reason"].startswith("Asks for 10+ years")


def test_second_run_only_checks_new_jobs(conn, settings):
    profile = load_profile(settings.profiles_dir, "alex")
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1)])
    run_filter(conn, profile)
    assert run_filter(conn, profile).checked == 0


def test_rerun_applies_a_changed_profile(conn, settings):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, description="You have 2 years of experience.")])
    run_filter(conn, load_profile(settings.profiles_dir, "alex"))
    assert store.status_counts(conn, "alex")["passed"] == 1

    path = settings.profiles_dir / "alex" / "profile.yaml"
    path.write_text(path.read_text().replace("drop_at_years: 3", "drop_at_years: 2"))
    summary = run_filter(conn, load_profile(settings.profiles_dir, "alex"), rerun=True)
    assert summary.dropped == 1 and store.status_counts(conn, "alex")["dropped"] == 1


def test_rerun_leaves_reposts_alone(conn, settings):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1), make_job(2, title="Junior AI Engineer 1")])
    summary = run_filter(conn, load_profile(settings.profiles_dir, "alex"), rerun=True)
    assert summary.checked == 1  # the re-post stays dropped as a re-post
