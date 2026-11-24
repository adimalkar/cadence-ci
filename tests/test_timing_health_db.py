"""SQL scoping, bounded reads, paging and consistent read-only diagnostics."""

import os
from dataclasses import replace

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence import timing_health as module
from cadence.db import apply_migrations
from cadence.ingest import upsert_job, upsert_repo, upsert_run
from cadence.models import Repo
from test_timing_health import REPO_ID, RUN_ID, job, run

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
REPO = Repo(REPO_ID, "timing-health", "rollback", False, "main")


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        upsert_repo(conn, REPO)
        yield conn
        conn.rollback()
        conn.execute("DELETE FROM repo WHERE id=%s", (REPO_ID,))
        conn.commit()


def test_automatic_paging_is_completed_only_and_explicit_ids_can_inspect_active(conn):
    for offset in range(4):
        upsert_run(
            conn, run(id=RUN_ID + offset, status="in_progress" if offset == 3 else "completed")
        )
    first = module.timing_health(conn, REPO_ID, limit=2)
    assert first["selected_run_ids"] == [RUN_ID + 2, RUN_ID + 1]
    assert first["next_before"] == RUN_ID + 1
    second = module.timing_health(conn, REPO_ID, before=first["next_before"], limit=2)
    assert second["selected_run_ids"] == [RUN_ID] and second["next_before"] is None
    explicit = module.timing_health(conn, REPO_ID, run_ids=[RUN_ID + 3, RUN_ID + 3])
    assert explicit["counts"] == {"pending": 1}


def test_only_current_attempt_jobs_and_their_steps_are_inspected(conn):
    upsert_run(conn, run(run_attempt=2))
    upsert_job(conn, job(created_at=None), REPO_ID)
    current = job(9_240_901, attempt=2)
    upsert_job(conn, current, REPO_ID)
    report = module.timing_health(conn, REPO_ID, run_ids=[RUN_ID])
    assert report["counts"] == {"consistent_observations": 1}
    assert report["runs"][0]["observed_jobs"] == report["runs"][0]["observed_steps"] == 1


def test_detail_budget_is_global_and_does_not_suppress_issue_counts(conn):
    for offset in range(3):
        upsert_run(conn, run(id=RUN_ID + offset))
    report = module.timing_health(conn, REPO_ID, max_issues=1)
    assert report["counts"] == {"incomplete": 3}
    assert report["issue_counts"] == {"no_current_attempt_jobs": 3}
    assert sum(len(r["issues"]) for r in report["runs"]) == 1
    assert report["details_truncated"]


def test_missing_or_cross_repository_run_ids_are_not_silently_ignored(conn):
    upsert_run(conn, run())
    for repo_id, ids in ((REPO_ID, [RUN_ID, RUN_ID + 99]), (REPO_ID + 1, [RUN_ID])):
        with pytest.raises(ValueError, match="not stored"):
            module.timing_health(conn, repo_id, run_ids=ids)


@pytest.mark.parametrize("bound", ["MAX_JOBS", "MAX_STEPS"])
def test_row_bounds_fail_without_a_partial_clean_report(conn, monkeypatch, bound):
    upsert_run(conn, run())
    upsert_job(conn, job(), REPO_ID)
    monkeypatch.setattr(module, bound, 0)
    with pytest.raises(ValueError, match="exceeds"):
        module.timing_health(conn, REPO_ID)


def test_inconsistent_job_repository_is_explicit_error(conn):
    upsert_run(conn, run())
    other = replace(REPO, id=REPO_ID + 1, name="other")
    upsert_repo(conn, other)
    upsert_job(conn, job(), other.id)
    with pytest.raises(ValueError, match="repository"):
        module.timing_health(conn, REPO_ID)


def test_real_read_only_snapshot_does_not_mutate_observations(conn):
    upsert_run(conn, run())
    upsert_job(conn, job(), REPO_ID)
    conn.commit()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    before = conn.execute("SELECT * FROM job WHERE id=%s", (job().id,)).fetchone()
    report = module.timing_health(conn, REPO_ID)
    assert report["counts"] == {"consistent_observations": 1}
    assert conn.execute("SELECT * FROM job WHERE id=%s", (job().id,)).fetchone() == before
    assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
