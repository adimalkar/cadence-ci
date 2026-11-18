"""Refresh persistence and selection against PostgreSQL; every write rolls back."""
import os
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.db.conn import apply_migrations
from cadence.ingest import ensure_run_stub, upsert_run
from cadence.models import Repo
from cadence.run_refresh import refresh_stored_run, select_run_ids
from test_run_refresh import T0
from test_run_refresh import snapshot as make_snapshot

REPO = Repo(7602051, "refresh-test", "rollback-only", False, "main")


def snapshot(run_id=7602100, **overrides):
    return make_snapshot(run_id, repo_id=overrides.pop("repo_id", REPO.id), **overrides)


TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        # Rollback-only fixture: no committed data or polling state is changed.
        conn.execute("INSERT INTO repo(id, owner, name, runs_etag, last_polled_at)"
                     " VALUES (%s, %s, %s, 'saved-etag', %s)",
                     (REPO.id, REPO.owner, REPO.name, T0))
        yield conn
        conn.rollback()


def test_refresh_preserves_poll_cursor_jobs_and_known_evidence(conn):
    upsert_run(conn, snapshot(pull_request_number=9, tree_sha="b" * 40))
    conn.execute("INSERT INTO job(id, run_id, repo_id, name, name_base, created_at, completed_at)"
                 " VALUES (7602900, 7602100, %s, 'test', 'test', %s, %s)", (REPO.id, T0, T0))
    conn.execute("INSERT INTO step(job_id, number, name, started_at, completed_at)"
                 " VALUES (7602900, 1, 'build', %s, %s)", (T0, T0))
    before_job = conn.execute("SELECT * FROM job WHERE id=7602900").fetchone()
    before_step = conn.execute("SELECT * FROM step WHERE job_id=7602900").fetchone()
    assert refresh_stored_run(conn, snapshot(workflow_name="New name", event="pull_request",
                              updated_at=T0 + timedelta(hours=1))) == "refreshed"
    row = conn.execute("SELECT * FROM run WHERE id=7602100").fetchone()
    assert row["workflow_name"] == "New name"
    assert row["event"] == "pull_request"
    assert row["pull_request_number"] == 9 and row["tree_sha"] == "b" * 40
    assert conn.execute("SELECT * FROM job WHERE id=7602900").fetchone() == before_job
    assert conn.execute("SELECT * FROM step WHERE job_id=7602900").fetchone() == before_step
    repo = conn.execute("SELECT * FROM repo WHERE id=%s", (REPO.id,)).fetchone()
    assert repo["runs_etag"] == "saved-etag" and repo["last_polled_at"] == T0


@pytest.mark.parametrize("overrides", [
    {"updated_at": T0 - timedelta(seconds=1)},
    {"run_attempt": 1, "updated_at": T0 + timedelta(hours=1)},
    {"status": "in_progress", "updated_at": T0 + timedelta(hours=1)},
])
def test_stale_or_regressing_snapshot_does_not_mutate(conn, overrides):
    upsert_run(conn, snapshot(run_attempt=2))
    old = conn.execute("SELECT * FROM run WHERE id=7602100").fetchone()
    incoming = {"run_attempt": 2, **overrides}
    assert refresh_stored_run(conn, snapshot(**incoming)) == "stale"
    assert conn.execute("SELECT * FROM run WHERE id=7602100").fetchone() == old


def test_new_attempt_is_accepted_and_deleted_run_is_not_recreated(conn):
    upsert_run(conn, snapshot())
    assert refresh_stored_run(conn, snapshot(run_attempt=2, status="queued",
                              updated_at=T0 + timedelta(hours=1))) == "refreshed"
    conn.execute("DELETE FROM run WHERE id=7602100")
    assert refresh_stored_run(conn, snapshot()) == "missing"
    assert conn.execute("SELECT count(*) AS n FROM run WHERE id=7602100").fetchone()["n"] == 0


def test_stub_gets_full_metadata(conn):
    # Job events can create a run before a workflow_run event arrives.
    ensure_run_stub(conn, snapshot())
    assert refresh_stored_run(conn, snapshot()) == "refreshed"
    row = conn.execute("SELECT * FROM run WHERE id=7602100").fetchone()
    assert row["workflow_id"] == 7 and row["status"] == "completed"
    assert row["workflow_path"] == ".github/workflows/ci.yml"


def test_repo_and_head_identity_conflicts_never_mutate(conn):
    upsert_run(conn, snapshot())
    old = conn.execute("SELECT * FROM run WHERE id=7602100").fetchone()
    for overrides in ({"repo_id": 7602999}, {"head_sha": "c" * 40}):
        with pytest.raises(ValueError):
            refresh_stored_run(conn, snapshot(**overrides))
        assert conn.execute("SELECT * FROM run WHERE id=7602100").fetchone() == old


def test_selection_is_bounded_ordered_and_scoped(conn):
    for run_id in (7602100, 7602102, 7602101):
        upsert_run(conn, snapshot(run_id))
    conn.execute("INSERT INTO repo(id,owner,name) VALUES (7602999,'other','repo')")
    upsert_run(conn, snapshot(7602200, repo_id=7602999))
    assert select_run_ids(conn, REPO.id, limit=2) == {
        "run_ids": [7602102, 7602101], "next_before": 7602101}
    assert select_run_ids(conn, REPO.id, before=7602101, limit=2) == {
        "run_ids": [7602100], "next_before": None}
    assert select_run_ids(conn, REPO.id, run_ids=[7602100, 7602102, 7602100]) == {
        "run_ids": [7602100, 7602102], "next_before": None}
    for ids in ([7602200], [9999]):
        with pytest.raises(ValueError, match="not stored"):
            select_run_ids(conn, REPO.id, run_ids=ids)


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"before": -1},
    {"run_ids": []}, {"run_ids": [True]}, {"run_ids": [7602100], "before": 99},
    {"run_ids": [7602100, 7602101], "limit": 1}])
def test_selection_validation(conn, kwargs):
    with pytest.raises(ValueError):
        select_run_ids(conn, REPO.id, **kwargs)
