"""Receipt persistence and live re-verification against PostgreSQL."""

import os
from dataclasses import replace
from datetime import datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.db import apply_migrations
from cadence.ingest import ingest_repo, upsert_job, upsert_repo, upsert_run
from cadence.job_inventory import job_coverage, store_inventory
from cadence.models import Run, RunPage
from test_job_inventory import REPO, RUN_ID, T0, inventory, job

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def run(**overrides):
    return replace(
        Run(
            RUN_ID,
            REPO.id,
            7,
            "ci.yml",
            "CI",
            1,
            1,
            "push",
            "completed",
            "success",
            "a" * 40,
            "main",
            T0,
            T0,
            T0,
        ),
        **overrides,
    )


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        upsert_repo(conn, REPO)
        upsert_run(conn, run())
        yield conn
        conn.rollback()
        conn.execute("DELETE FROM repo WHERE id=%s", (REPO.id,))
        conn.commit()


def state(conn):
    return job_coverage(conn, REPO.id, run_ids=[RUN_ID])["runs"][0]


def seed(conn):
    upsert_job(conn, job(), REPO.id)
    assert store_inventory(conn, inventory())


def test_legacy_rows_unknown_until_terminal_inventory_is_recorded(conn):
    upsert_job(conn, job(), REPO.id)
    assert state(conn)["coverage"] == "unknown"
    assert store_inventory(conn, inventory())
    report = state(conn)
    assert report["coverage"] == "verified" and report["expected_count"] == 1
    assert report["missing_job_ids"] == [] and report["extra_job_ids"] == []


def test_zero_inventory_is_verified_empty_not_unknown(conn):
    assert store_inventory(conn, inventory([]))
    assert state(conn)["coverage"] == "verified"
    assert state(conn)["expected_count"] == 0
    assert state(conn)["stored_count"] == 0


def test_membership_change_is_detected_even_when_counts_match(conn):
    seed(conn)
    conn.execute("DELETE FROM job WHERE run_id=%s", (RUN_ID,))
    upsert_job(conn, job(9_760_901), REPO.id)
    report = state(conn)
    assert report["coverage"] == "inventory_mismatch"
    assert report["expected_count"] == report["stored_count"] == 1
    assert report["missing_job_ids"] == [9_760_900]
    assert report["extra_job_ids"] == [9_760_901]


def test_later_attempt_does_not_inherit_an_earlier_receipt(conn):
    seed(conn)
    upsert_run(conn, run(run_attempt=2))
    upsert_job(conn, job(9_760_901, attempt=2), REPO.id)
    assert state(conn)["coverage"] == "unknown"
    second = inventory([job(9_760_901, attempt=2)], attempt=2)
    assert store_inventory(conn, second)
    assert state(conn)["coverage"] == "verified"
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM run_job_inventory WHERE run_id=%s", (RUN_ID,)
        ).fetchone()["n"]
        == 2
    )


@pytest.mark.parametrize("target,expected", [("job", "jobs_incomplete"), ("run", "run_incomplete")])
def test_receipt_does_not_hide_later_incomplete_status(conn, target, expected):
    seed(conn)
    conn.execute(
        f"UPDATE {target} SET status='in_progress' WHERE "
        + ("run_id=%s" if target == "job" else "id=%s"),
        (RUN_ID,),
    )
    assert state(conn)["coverage"] == expected


def test_nonterminal_or_inconsistent_inventory_is_not_recorded(conn):
    upsert_job(conn, job(status="in_progress"), REPO.id)
    assert not store_inventory(conn, inventory([job(status="in_progress")]))
    assert state(conn)["coverage"] == "unknown"
    with pytest.raises(ValueError, match="stored jobs"):
        store_inventory(conn, inventory())
    with pytest.raises(ValueError, match="stored run"):
        store_inventory(conn, inventory(repo_id=REPO.id + 1))


def test_older_or_conflicting_receipt_does_not_replace_evidence(conn):
    seed(conn)
    assert not store_inventory(conn, inventory(observed_at=T0 - timedelta(seconds=1)))
    assert datetime.fromisoformat(state(conn)["observed_at"]) == T0
    conn.execute("DELETE FROM job WHERE run_id=%s", (RUN_ID,))
    upsert_job(conn, job(9_760_901), REPO.id)
    assert not store_inventory(conn, inventory([job(9_760_901)]))
    assert state(conn)["coverage"] == "inventory_mismatch"


def test_receipt_and_jobs_rollback_together_and_cascade_with_run(conn):
    with pytest.raises(RuntimeError), conn.transaction():
        seed(conn)
        raise RuntimeError("abort transaction")
    assert state(conn)["coverage"] == "unknown"
    seed(conn)
    conn.execute("DELETE FROM run WHERE id=%s", (RUN_ID,))
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM run_job_inventory WHERE run_id=%s", (RUN_ID,)
        ).fetchone()["n"]
        == 0
    )


def test_report_scoping_order_limit_and_read_only(conn):
    for index in (1, 2):
        upsert_run(conn, run(id=RUN_ID + index))
    assert job_coverage(conn, REPO.id, limit=1)["truncated"]
    assert job_coverage(conn, REPO.id, limit=1)["runs"][0]["run_id"] == RUN_ID + 2
    with pytest.raises(ValueError, match="not stored"):
        job_coverage(conn, REPO.id + 1, run_ids=[RUN_ID])
    conn.commit()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    assert job_coverage(conn, REPO.id)["counts"] == {"unknown": 3}


class LegacyProvider:
    def __init__(self, fail=False):
        self.fail = fail

    async def get_repo(self, owner, name):
        return REPO

    async def fetch_runs(self, repo, **kwargs):
        return RunPage([run()], '"fresh"') if kwargs["page"] == 1 else RunPage([], None)

    async def fetch_jobs(self, repo, run_id, *, attempt=None):
        if self.fail:
            raise ValueError("partial/malformed job fetch")
        return [job()]


class InventoryProvider(LegacyProvider):
    async def fetch_job_inventory(self, repo, run_id, *, attempt):
        assert run_id == RUN_ID and attempt == 1
        if self.fail:
            raise ValueError("partial inventory")
        return inventory()


async def test_ingest_uses_optional_receipt_capability_and_legacy_stays_unknown(conn):
    stats = await ingest_repo(LegacyProvider(), conn, REPO.owner, REPO.name, limit=1)
    assert stats.jobs_written == 1 and stats.inventories_written == 0
    assert state(conn)["coverage"] == "unknown"
    conn.execute("UPDATE repo SET runs_etag=NULL WHERE id=%s", (REPO.id,))
    conn.commit()
    stats = await ingest_repo(InventoryProvider(), conn, REPO.owner, REPO.name, limit=1)
    assert stats.inventories_written == 1 and stats.jobs_failed == 0
    assert state(conn)["coverage"] == "verified"


@pytest.mark.parametrize("provider_type", [LegacyProvider, InventoryProvider])
async def test_non_rate_failure_does_not_advance_etag(conn, provider_type):
    stats = await ingest_repo(provider_type(fail=True), conn, REPO.owner, REPO.name, limit=1)
    assert stats.jobs_failed == 1 and stats.jobs_written == 0
    assert "1 failed job fetches" in str(stats)
    row = conn.execute("SELECT runs_etag FROM repo WHERE id=%s", (REPO.id,)).fetchone()
    assert row["runs_etag"] is None
    stats = await ingest_repo(provider_type(), conn, REPO.owner, REPO.name, limit=1)
    assert not stats.not_modified
    assert (
        conn.execute("SELECT runs_etag FROM repo WHERE id=%s", (REPO.id,)).fetchone()["runs_etag"]
        == '"fresh"'
    )


def test_zero_receipt_does_not_follow_run_to_another_repo(conn):
    assert store_inventory(conn, inventory([]))
    other = replace(REPO, id=REPO.id + 1, name="other")
    upsert_repo(conn, other)
    conn.execute("UPDATE run SET repo_id=%s WHERE id=%s", (other.id, RUN_ID))
    assert (
        job_coverage(conn, other.id, run_ids=[RUN_ID])["runs"][0]["coverage"] == "identity_mismatch"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 501},
        {"run_ids": []},
        {"run_ids": [True]},
        {"run_ids": [RUN_ID, RUN_ID + 1], "limit": 1},
    ],
)
def test_coverage_validates_bounds_before_query(conn, kwargs):
    with pytest.raises(ValueError):
        job_coverage(conn, REPO.id, **kwargs)


async def test_all_rerun_attempts_receive_distinct_receipts(conn):
    class RerunProvider(InventoryProvider):
        async def fetch_runs(self, repo, **kwargs):
            return RunPage([run(run_attempt=2)], '"fresh"')

        async def fetch_job_inventory(self, repo, run_id, *, attempt):
            item = job(9_760_900 + attempt, attempt=attempt)
            return inventory([item], attempt=attempt)

    stats = await ingest_repo(RerunProvider(), conn, REPO.owner, REPO.name, limit=1)
    assert stats.inventories_written == 2 and stats.jobs_written == 2
    assert state(conn)["attempt"] == 2 and state(conn)["coverage"] == "verified"
