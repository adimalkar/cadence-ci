"""Worker event-handling tests, run against a real Postgres.

Covers a bug found via live verification: a workflow_job webhook can arrive with no
corresponding workflow_run row yet -- GitHub does not guarantee delivery order between
the two event types, and job.run_id has a foreign key to run. The fix is a stub run
insert that can never outrank a real one.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from cadence import config
from cadence.db.conn import apply_migrations
from cadence.ingest import ensure_run_stub, upsert_run
from cadence.models import Run
from cadence.providers.github import GitHubProvider
from cadence.worker import _handle_webhook_event

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


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    # _handle_webhook_event opens its own connect(), which reads settings.database_url
    # fresh each call -- route it at the test DB for the duration of each test only.
    monkeypatch.setattr(config.settings, "database_url", TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB) as c:
        yield c
        c.execute("DELETE FROM job")
        c.execute("DELETE FROM run")
        c.execute("DELETE FROM repo")
        c.commit()


def _seed_repo(conn, repo_id: int = 777) -> int:
    conn.execute(
        "INSERT INTO repo (id, owner, name) VALUES (%s, 'acme', 'widget')"
        " ON CONFLICT (id) DO NOTHING",
        (repo_id,),
    )
    conn.commit()
    return repo_id


def _full_run(repo_id: int, run_id: int = 100, **over) -> Run:
    base = dict(
        id=run_id, repo_id=repo_id, workflow_id=7, workflow_path=".github/workflows/ci.yml",
        workflow_name="CI", run_number=55, run_attempt=1, event="push", status="completed",
        conclusion="success", head_sha="realsha1234", head_branch="main",
        created_at=datetime(2026, 8, 1, tzinfo=UTC), started_at=None, updated_at=None,
    )
    base.update(over)
    return Run(**base)


def _job_payload(repo_id: int, run_id: int = 100) -> dict:
    return {
        "action": "completed",
        "repository": {
            "id": repo_id, "owner": {"login": "acme"}, "name": "widget", "private": False
        },
        "workflow_job": {
            "id": 1, "run_id": run_id, "name": "test", "status": "completed",
            "conclusion": "success", "labels": ["ubuntu-latest"], "run_attempt": 1,
            "workflow_name": "CI", "head_sha": "abc123", "head_branch": "main",
            "created_at": "2026-08-01T10:00:00Z", "started_at": "2026-08-01T10:00:05Z",
            "completed_at": "2026-08-01T10:01:00Z", "steps": [],
        },
    }


async def _deliver(event: str, body: dict, *, delivery_id: str | None = None) -> str:
    delivery_id = delivery_id or str(uuid.uuid4())
    job_row = {
        "id": 1,
        "kind": "webhook_event",
        "payload": {"event": event, "delivery_id": delivery_id, "body": body},
    }
    provider = GitHubProvider("unused-token")
    try:
        await _handle_webhook_event(provider, job_row)
    finally:
        await provider.aclose()
    return delivery_id


class TestEnsureRunStub:
    def test_creates_a_row_when_none_exists(self, conn):
        repo_id = _seed_repo(conn)
        stub = Run(
            id=100, repo_id=repo_id, workflow_id=None, workflow_path=None,
            workflow_name="CI", run_number=None, run_attempt=1, event=None, status=None,
            conclusion=None, head_sha="stubsha", head_branch="main",
            created_at=datetime(2026, 8, 1, tzinfo=UTC), started_at=None, updated_at=None,
        )
        ensure_run_stub(conn, stub)
        conn.commit()
        row = conn.execute("SELECT head_sha, status FROM run WHERE id = 100").fetchone()
        assert row[0] == "stubsha"
        assert row[1] is None

    def test_never_overwrites_an_existing_fuller_row(self, conn):
        """The entire safety property: a job-derived stub must never win against a
        real run row."""
        repo_id = _seed_repo(conn)
        upsert_run(conn, _full_run(repo_id))
        conn.commit()

        stub = Run(
            id=100, repo_id=repo_id, workflow_id=None, workflow_path=None,
            workflow_name=None, run_number=None, run_attempt=1, event=None, status=None,
            conclusion=None, head_sha="stub-should-not-land", head_branch=None,
            created_at=datetime(2026, 8, 1, tzinfo=UTC), started_at=None, updated_at=None,
        )
        ensure_run_stub(conn, stub)
        conn.commit()

        row = conn.execute(
            "SELECT head_sha, status, conclusion FROM run WHERE id = 100"
        ).fetchone()
        assert row[0] == "realsha1234"  # untouched
        assert row[1] == "completed"
        assert row[2] == "success"


class TestHandleWebhookEvent:
    """Drives the exact function the live webhook worker calls, against real-shaped
    payloads."""

    async def test_job_event_with_no_prior_run_creates_stub_then_job(self, conn):
        """The scenario that failed live: a workflow_job event for a run this database
        has never seen a workflow_run event for."""
        repo_id = _seed_repo(conn)
        await _deliver("workflow_job", _job_payload(repo_id))

        run_row = conn.execute("SELECT head_sha FROM run WHERE id = 100").fetchone()
        job_row = conn.execute("SELECT id, name FROM job WHERE run_id = 100").fetchone()
        assert run_row is not None
        assert run_row[0] == "abc123"
        assert job_row is not None
        assert job_row[1] == "test"

    async def test_job_event_after_real_run_does_not_clobber_it(self, conn):
        repo_id = _seed_repo(conn)
        upsert_run(conn, _full_run(repo_id))
        conn.commit()

        await _deliver("workflow_job", _job_payload(repo_id))

        run_row = conn.execute("SELECT head_sha, status FROM run WHERE id = 100").fetchone()
        assert run_row[0] == "realsha1234"  # the job event's stub data did not win
        assert run_row[1] == "completed"

    async def test_marks_delivery_processed(self, conn):
        repo_id = _seed_repo(conn)
        delivery_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO webhook_delivery (delivery_id, event) VALUES (%s, 'workflow_job')",
            (delivery_id,),
        )
        conn.commit()

        await _deliver("workflow_job", _job_payload(repo_id), delivery_id=delivery_id)

        row = conn.execute(
            "SELECT processed_at IS NOT NULL FROM webhook_delivery WHERE delivery_id = %s",
            (delivery_id,),
        ).fetchone()
        assert row[0] is True
