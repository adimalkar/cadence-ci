"""Queue tests, run against a real Postgres. Skipped when CADENCE_TEST_DATABASE_URL is
unset, matching the pattern in test_schema.py."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cadence import queue
from cadence.db.conn import apply_migrations

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no test database")


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB) as c:
        yield c
        c.execute("DELETE FROM ingest_job")
        c.commit()


class TestClaiming:
    def test_claim_returns_pending_job(self, conn):
        job_id = queue.enqueue(conn, "poll_repo", {"owner": "a", "name": "b"})
        conn.commit()
        claimed = queue.claim_next(conn)
        assert claimed["id"] == job_id
        assert claimed["status"] == "processing"
        assert claimed["payload"] == {"owner": "a", "name": "b"}

    def test_claim_skips_future_scheduled_jobs(self, conn):
        queue.enqueue(conn, "poll_repo", {"owner": "a", "name": "b"})
        queue.enqueue(
            conn, "poll_repo", {"owner": "c", "name": "d"},
            run_at=datetime.now(UTC) + timedelta(hours=1),
        )
        conn.commit()
        claimed = queue.claim_next(conn)
        assert claimed["payload"]["owner"] == "a"
        assert queue.claim_next(conn) is None  # the future job isn't claimable yet

    def test_claim_returns_none_when_empty(self, conn):
        assert queue.claim_next(conn) is None

    def test_concurrent_claims_never_return_the_same_row(self, conn):
        """The property SKIP LOCKED exists for: two workers racing on the same table
        must never both claim the same job. This is the reason the queue needs no
        separate broker."""
        for i in range(5):
            queue.enqueue(conn, "poll_repo", {"owner": f"r{i}", "name": "x"})
        conn.commit()

        claimed_ids = set()
        with psycopg.connect(TEST_DB) as worker_conn:
            for _ in range(5):
                claimed = queue.claim_next(worker_conn)
                assert claimed is not None
                claimed_ids.add(claimed["id"])
                worker_conn.commit()
            assert len(claimed_ids) == 5
            assert queue.claim_next(worker_conn) is None


class TestCompleteAndFail:
    def test_complete_marks_done(self, conn):
        job_id = queue.enqueue(conn, "poll_repo", {})
        conn.commit()
        queue.claim_next(conn)
        queue.complete(conn, job_id)
        conn.commit()
        row = conn.execute("SELECT status FROM ingest_job WHERE id = %s", (job_id,)).fetchone()
        assert row[0] == "done"

    def test_fail_with_retry_reschedules_forward(self, conn):
        job_id = queue.enqueue(conn, "poll_repo", {})
        conn.commit()
        queue.claim_next(conn)
        queue.fail(conn, job_id, "boom", retry_in=timedelta(minutes=5))
        conn.commit()
        row = conn.execute(
            "SELECT status, attempts, run_at > now() FROM ingest_job WHERE id = %s", (job_id,)
        ).fetchone()
        assert row[0] == "pending"  # eligible to be claimed again
        assert row[1] == 1
        assert row[2] is True

    def test_fail_without_retry_is_terminal(self, conn):
        job_id = queue.enqueue(conn, "poll_repo", {})
        conn.commit()
        queue.claim_next(conn)
        queue.fail(conn, job_id, "boom", retry_in=None)
        conn.commit()
        row = conn.execute("SELECT status FROM ingest_job WHERE id = %s", (job_id,)).fetchone()
        assert row[0] == "failed"


class TestPendingOrProcessingCount:
    def test_future_jobs_dont_count_as_claimable(self, conn):
        """The property `worker run --until-empty` depends on: a recurring poll_repo
        continuation scheduled 30 minutes out must not keep the drain loop spinning
        forever waiting for it."""
        queue.enqueue(
            conn, "poll_repo", {}, run_at=datetime.now(UTC) + timedelta(hours=1)
        )
        conn.commit()
        assert queue.pending_or_processing_count(conn) == 0

    def test_due_and_processing_jobs_both_count(self, conn):
        queue.enqueue(conn, "poll_repo", {})
        conn.commit()
        assert queue.pending_or_processing_count(conn) == 1
        queue.claim_next(conn)  # now 'processing', not 'pending'
        conn.commit()
        assert queue.pending_or_processing_count(conn) == 1
