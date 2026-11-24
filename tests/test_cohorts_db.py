"""Real SQL selection, repository isolation and read-only comparison."""

import os
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.rows import dict_row
from typer.testing import CliRunner

from cadence import cli, cohorts
from cadence.db import apply_migrations
from cadence.ingest import upsert_job, upsert_run
from test_cohorts import REPO_ID, WORKFLOW, run

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
BEFORE = [8_650_101, 8_650_102]
AFTER = [8_650_103, 8_650_104]


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        conn.execute(
            "INSERT INTO repo(id, owner, name) VALUES (%s,'cohort-test','rollback')", (REPO_ID,)
        )
        for day, run_id in enumerate(BEFORE + AFTER):
            sample = run(run_id, day=day)
            upsert_run(conn, sample)
            for job in sample.jobs:
                upsert_job(conn, job, REPO_ID)
        yield conn
        conn.rollback()
        # One integration case commits fixtures to let CLI use separate connections.
        conn.execute("DELETE FROM repo WHERE id=%s", (REPO_ID,))
        conn.commit()


def test_real_query_works_in_read_only_transaction(conn):
    conn.commit()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    result = cohorts.compare_stored_runs(conn, REPO_ID, WORKFLOW, BEFORE, AFTER, min_samples=2)
    assert result["comparisons"][0]["before"]["run_ids"] == BEFORE
    assert result["comparisons"][0]["metrics"]["wall_seconds"]["change"] == 0
    assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"


@pytest.mark.parametrize("ids", [[9999999], [BEFORE[0], 9999999]])
def test_missing_or_other_repository_id_is_rejected(conn, ids):
    with pytest.raises(ValueError, match="not stored for this repository"):
        cohorts.compare_stored_runs(conn, REPO_ID + 1, WORKFLOW, ids, AFTER, min_samples=2)


def test_job_read_bound_fails_instead_of_silent_truncation(conn, monkeypatch):
    monkeypatch.setattr(cohorts, "MAX_JOBS", 1)
    with pytest.raises(ValueError, match="job bound"):
        cohorts.compare_stored_runs(conn, REPO_ID, WORKFLOW, BEFORE, AFTER, min_samples=2)


def test_incomplete_and_mixed_attempt_jobs_are_explained(conn):
    conn.execute("UPDATE job SET attempt=2 WHERE run_id=%s", (BEFORE[0],))
    conn.execute("UPDATE job SET started_at=NULL WHERE run_id=%s", (AFTER[0],))
    result = cohorts.compare_stored_runs(conn, REPO_ID, WORKFLOW, BEFORE, AFTER, min_samples=2)
    assert not result["comparisons"]
    assert {r["reason"] for r in result["excluded_runs"]} == {
        "mixed_job_attempts",
        "missing_job_timing",
        "insufficient_samples",
    }


def test_real_cli_reads_snapshot_offline_without_changing_rows(conn, monkeypatch, tmp_path):
    conn.commit()

    @contextmanager
    def connect():
        with psycopg.connect(TEST_DB, row_factory=dict_row) as db:
            yield db

    def forbidden(*args, **kwargs):
        raise AssertionError("offline comparison attempted GitHub access")

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "_require_token", forbidden)
    monkeypatch.setattr(cli, "GitHubProvider", forbidden)
    before = conn.execute("SELECT * FROM run WHERE repo_id=%s ORDER BY id", (REPO_ID,)).fetchall()
    output = tmp_path / "comparison.json"
    args = [
        "compare-runs",
        "cohort-test/rollback",
        "--workflow",
        WORKFLOW,
        "--min-samples",
        "2",
        "--json",
        str(output),
    ]
    for option, ids in (("--before-run", BEFORE), ("--after-run", AFTER)):
        for run_id in ids:
            args += [option, str(run_id)]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "Observed timing changes" in result.output
    assert output.is_file()
    assert (
        conn.execute("SELECT * FROM run WHERE repo_id=%s ORDER BY id", (REPO_ID,)).fetchall()
        == before
    )
