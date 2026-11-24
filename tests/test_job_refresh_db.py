"""Repair atomicity, immutable identities and polling isolation against PostgreSQL."""

import os
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.db import apply_migrations
from cadence.ingest import upsert_job, upsert_repo, upsert_run
from cadence.job_refresh import persist_jobs, select_targets
from cadence.models import Run
from test_job_refresh import REPO, RUN_ID, T0, job, target

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def run(run_id=RUN_ID, **overrides):
    values = dict(
        id=run_id,
        repo_id=REPO.id,
        workflow_id=7,
        workflow_path="ci.yml",
        workflow_name="CI",
        run_number=1,
        run_attempt=1,
        event="push",
        status="completed",
        conclusion="success",
        head_sha="a" * 40,
        head_branch="main",
        created_at=T0,
        started_at=T0,
        updated_at=target().updated_at,
    )
    return Run(**{**values, **overrides})


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        upsert_repo(conn, REPO)
        upsert_run(conn, run())
        conn.execute(
            "UPDATE repo SET runs_etag='unchanged',last_polled_at=%s WHERE id=%s", (T0, REPO.id)
        )
        yield conn
        conn.rollback()


def test_job_and_step_timings_are_repaired_without_changing_run_or_poll_state(conn):
    partial = job(created_at=None, started_at=None, completed_at=None)
    partial.steps[0].started_at = None
    upsert_job(conn, partial, REPO.id)
    before_run = conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone()
    before_repo = conn.execute("SELECT * FROM repo WHERE id=%s", (REPO.id,)).fetchone()
    result = persist_jobs(conn, target(), [job()])
    assert result == {"status": "refreshed", "jobs_written": 1, "steps_written": 1}
    saved = conn.execute("SELECT * FROM job WHERE id=%s", (job().id,)).fetchone()
    step = conn.execute("SELECT * FROM step WHERE job_id=%s", (job().id,)).fetchone()
    assert saved["created_at"] == T0 and saved["completed_at"] == job().completed_at
    assert step["started_at"] == job().steps[0].started_at
    assert conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone() == before_run
    assert conn.execute("SELECT * FROM repo WHERE id=%s", (REPO.id,)).fetchone() == before_repo


def test_omitted_timestamps_and_unreturned_rows_are_preserved(conn):
    upsert_job(conn, job(), REPO.id)
    upsert_job(conn, job(7_880_901), REPO.id)
    incoming = job(created_at=None, started_at=None, completed_at=None)
    incoming.steps[0].started_at = incoming.steps[0].completed_at = None
    persist_jobs(conn, target(), [incoming])
    saved = conn.execute("SELECT * FROM job WHERE id=%s", (incoming.id,)).fetchone()
    assert saved["completed_at"] == job().completed_at and saved["created_at"] == T0
    assert (
        conn.execute("SELECT started_at FROM step WHERE job_id=%s", (incoming.id,)).fetchone()[
            "started_at"
        ]
        == job().steps[0].started_at
    )
    assert (
        conn.execute("SELECT count(*) AS n FROM job WHERE run_id=%s", (RUN_ID,)).fetchone()["n"]
        == 2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_attempt", 2),
        ("status", "in_progress"),
        ("head_sha", "b" * 40),
        ("updated_at", T0 + timedelta(hours=1)),
    ],
)
def test_changed_run_gets_no_job_writes(conn, field, value):
    # Parameterized field names are fixture constants, never command input.
    conn.execute(f"UPDATE run SET {field}=%s WHERE id=%s", (value, RUN_ID))
    assert persist_jobs(conn, target(), [job()]) == {"status": "run_changed"}
    assert (
        conn.execute("SELECT count(*) AS n FROM job WHERE run_id=%s", (RUN_ID,)).fetchone()["n"]
        == 0
    )


def test_missing_run_is_not_recreated_and_empty_list_never_deletes(conn):
    upsert_job(conn, job(), REPO.id)
    assert persist_jobs(conn, target(), [])["status"] == "empty"
    assert (
        conn.execute("SELECT count(*) AS n FROM job WHERE run_id=%s", (RUN_ID,)).fetchone()["n"]
        == 1
    )
    conn.execute("DELETE FROM run WHERE id=%s", (RUN_ID,))
    assert persist_jobs(conn, target(), [job()]) == {"status": "missing"}


def test_late_identity_conflict_rolls_back_earlier_job_and_steps(conn):
    upsert_run(conn, run(RUN_ID + 1))
    upsert_job(conn, job(7_880_901, run_id=RUN_ID + 1), REPO.id)
    with pytest.raises(ValueError, match="identity"), conn.transaction():
        persist_jobs(conn, target(), [job(), job(7_880_901)])
    assert conn.execute("SELECT id FROM job WHERE id=%s", (job().id,)).fetchone() is None
    assert conn.execute("SELECT job_id FROM step WHERE job_id=%s", (job().id,)).fetchone() is None
    assert (
        conn.execute("SELECT run_id FROM job WHERE id=7880901").fetchone()["run_id"] == RUN_ID + 1
    )


@pytest.mark.parametrize("overrides", [{"attempt": 2}, {"run_id": RUN_ID + 1}])
def test_shared_upsert_rejects_identity_conflict_before_step_changes(conn, overrides):
    upsert_job(conn, job(), REPO.id)
    old = conn.execute("SELECT * FROM job WHERE id=%s", (job().id,)).fetchone()
    with pytest.raises(ValueError, match="identity"):
        upsert_job(conn, job(**overrides), REPO.id)
    assert conn.execute("SELECT * FROM job WHERE id=%s", (job().id,)).fetchone() == old


def test_selection_scopes_and_deduplicates_explicit_attempts(conn):
    upsert_run(conn, run(RUN_ID + 1, run_attempt=2))
    targets = select_targets(conn, REPO.id, [RUN_ID + 1, RUN_ID, RUN_ID + 1])
    assert [(t.run_id, t.attempt) for t in targets] == [(RUN_ID + 1, 2), (RUN_ID, 1)]
    assert select_targets(conn, REPO.id, [RUN_ID + 1], attempt=1)[0].attempt == 1
    for ids, attempt in (([RUN_ID], 2), ([RUN_ID + 5], None)):
        with pytest.raises(ValueError):
            select_targets(conn, REPO.id, ids, attempt=attempt)
    with pytest.raises(ValueError):
        select_targets(conn, REPO.id + 1, [RUN_ID])
