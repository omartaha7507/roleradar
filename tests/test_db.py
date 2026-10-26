from roleradar import store
from roleradar.db import MIGRATIONS, migrate, schema_version

from .conftest import make_job


def test_migrate_is_idempotent(conn):
    assert schema_version(conn) == len(MIGRATIONS)
    assert migrate(conn) == len(MIGRATIONS)


def test_ingest_inserts_then_updates(conn):
    first = store.ingest_jobs(conn, "alex", "linkedin", [make_job(1), make_job(2)])
    assert (first.inserted, first.updated, first.reposts) == (2, 0, 0)

    again = store.ingest_jobs(conn, "alex", "linkedin", [make_job(1)])
    assert (again.inserted, again.updated) == (0, 1)
    assert store.status_counts(conn, "alex")["new"] == 2


def test_longer_description_wins(conn):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, description="short")])
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, description="a much longer description")])
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, description="tiny")])
    job = store.list_jobs(conn, "alex")[0][0]
    assert store.get_job(conn, "alex", job["id"])["description"] == "a much longer description"


def test_repost_under_new_url_is_dropped(conn):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1, title="Junior AI Engineer (m/w/d)")])
    result = store.ingest_jobs(conn, "alex", "linkedin", [make_job(2, title="Junior AI Engineer")])
    assert result.reposts == 1
    newest = store.list_jobs(conn, "alex")[0][0]
    assert newest["status"] == "dropped"
    assert newest["status_reason"].startswith("Re-post of job #")


def test_repost_of_applied_job_says_so(conn):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1)])
    conn.execute("UPDATE jobs SET status = 'applied'")
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(2, title="Junior AI Engineer 1")])
    newest = store.list_jobs(conn, "alex")[0][0]
    assert newest["status_reason"].startswith("Already applied")


def test_profiles_never_see_each_other(conn):
    store.ingest_jobs(conn, "alex", "linkedin", [make_job(1)])
    store.ingest_jobs(conn, "sara", "linkedin", [make_job(1)])  # same job, other person
    assert store.list_jobs(conn, "alex")[1] == 1
    assert store.list_jobs(conn, "sara")[1] == 1
    assert store.list_jobs(conn, "sara")[0][0]["status"] == "new"  # not a re-post for her


def test_failed_batch_writes_nothing(conn):
    bad = make_job(2)
    del bad["title"]  # KeyError halfway through the batch
    try:
        store.ingest_jobs(conn, "alex", "linkedin", [make_job(1), bad])
    except KeyError:
        pass
    assert store.list_jobs(conn, "alex")[1] == 0
