"""Ingest-path tests against a real Postgres.

These pin down the four silent data-fidelity bugs found in the Phase 0 audit. All four
share a shape worth naming: nothing raised, nothing logged, and the pipeline kept
reporting success while quietly dropping data that GitHub deletes after 90 days.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cadence.db.conn import apply_migrations
from cadence.ingest import ingest_repo, upsert_job, upsert_run
from cadence.models import Job, Repo, Run, RunPage, Step
from cadence.providers.base import RateLimited

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
        c.execute("DELETE FROM job")
        c.execute("DELETE FROM run")
        c.execute("DELETE FROM repo")
        c.commit()


REPO = Repo(id=4242, owner="acme", name="widget", is_private=False, default_branch="main")
T0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _run(run_id: int = 1, *, attempt: int = 1, started_at=None, **over) -> Run:
    base = dict(
        id=run_id, repo_id=REPO.id, workflow_id=7, workflow_path=".github/workflows/ci.yml",
        workflow_name="CI", run_number=1, run_attempt=attempt, event="push",
        status="completed", conclusion="success", head_sha="abc", head_branch="main",
        created_at=T0, started_at=started_at, updated_at=T0,
    )
    base.update(over)
    return Run(**base)


def _job(job_id: int, run_id: int = 1, *, started_at=None, attempt: int = 1, **over) -> Job:
    base = dict(
        id=job_id, run_id=run_id, name="test", name_base="test", status="completed",
        conclusion="success", runner_labels=["ubuntu-latest"], runner_group=None,
        created_at=T0, started_at=started_at, completed_at=T0 + timedelta(minutes=5),
        attempt=attempt, steps=[],
    )
    base.update(over)
    return Job(**base)


class FakeProvider:
    """Stands in for GitHubProvider. Records which attempts were asked for, and can be
    told to fail job fetching so the ETag-ordering property is testable."""

    name = "fake"

    def __init__(self, runs: list[Run], *, fail_jobs: bool = False, etag: str = 'W/"v1"'):
        self._runs = runs
        self._fail_jobs = fail_jobs
        self._etag = etag
        self.jobs_calls: list[tuple[int, int | None]] = []
        self.runs_calls = 0

    async def get_repo(self, owner: str, name: str) -> Repo:
        return REPO

    async def fetch_runs(self, repo, *, page=1, per_page=100, etag=None) -> RunPage:
        self.runs_calls += 1
        if etag == self._etag:
            return RunPage(runs=[], etag=etag, not_modified=True)
        if page > 1:
            return RunPage(runs=[], etag=None)
        return RunPage(runs=list(self._runs), etag=self._etag)

    async def fetch_jobs(self, repo, run_id: int, *, attempt: int | None = None) -> list[Job]:
        self.jobs_calls.append((run_id, attempt))
        if self._fail_jobs:
            raise RateLimited(60.0, "simulated")
        # attempt=None means GitHub's filter=latest, which returns the *newest* attempt
        # -- the exact behaviour that was hiding a rerun's earlier, failing jobs.
        run = next((r for r in self._runs if r.id == run_id), None)
        effective = attempt if attempt is not None else (run.run_attempt if run else 1)
        jid = run_id * 1000 + effective
        return [_job(jid, run_id, started_at=T0, attempt=effective)]

    async def fetch_logs(self, repo, job_id: int) -> bytes | None:
        return None

    def normalize_event(self, event: str, payload: dict):
        return None

    async def aclose(self) -> None:
        return None


class TestUpsertPreservesTimestamps:
    """A job arrives up to three times over webhooks (queued -> in_progress ->
    completed) and only the later payloads carry started_at. The original ON CONFLICT
    omitted it, so webhook-ingested jobs kept started_at NULL forever -- silently
    voiding execution_time, queue_time, and every billing figure downstream."""

    def test_job_started_at_is_filled_in_by_a_later_update(self, conn):
        conn.execute(
            "INSERT INTO repo (id, owner, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (REPO.id, REPO.owner, REPO.name),
        )
        upsert_run(conn, _run())
        upsert_job(conn, _job(900, started_at=None, status="queued", conclusion=None), REPO.id)
        conn.commit()
        assert conn.execute("SELECT started_at FROM job WHERE id=900").fetchone()[0] is None

        upsert_job(conn, _job(900, started_at=T0 + timedelta(seconds=30)), REPO.id)
        conn.commit()
        got = conn.execute("SELECT started_at FROM job WHERE id=900").fetchone()[0]
        assert got == T0 + timedelta(seconds=30)

    def test_a_null_started_at_never_erases_a_real_one(self, conn):
        """The coalesce must point this way round: an out-of-order or partial payload
        must not blank a timestamp already recorded."""
        conn.execute(
            "INSERT INTO repo (id, owner, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (REPO.id, REPO.owner, REPO.name),
        )
        upsert_run(conn, _run())
        upsert_job(conn, _job(901, started_at=T0), REPO.id)
        conn.commit()

        upsert_job(conn, _job(901, started_at=None), REPO.id)
        conn.commit()
        assert conn.execute("SELECT started_at FROM job WHERE id=901").fetchone()[0] == T0

    def test_run_started_at_is_filled_in_by_a_later_update(self, conn):
        conn.execute(
            "INSERT INTO repo (id, owner, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (REPO.id, REPO.owner, REPO.name),
        )
        upsert_run(conn, _run(started_at=None, status="queued", conclusion=None))
        conn.commit()
        assert conn.execute("SELECT started_at FROM run WHERE id=1").fetchone()[0] is None

        upsert_run(conn, _run(started_at=T0 + timedelta(seconds=5)))
        conn.commit()
        got = conn.execute("SELECT started_at FROM run WHERE id=1").fetchone()[0]
        assert got == T0 + timedelta(seconds=5)


class TestEtagOrdering:
    async def test_etag_is_not_stored_when_job_fetch_fails(self, conn):
        """The cursor must not advance past runs whose jobs were never written. Storing
        it early meant the retry 304'd, returned early, and the step timings for that
        window were lost for good."""
        provider = FakeProvider([_run(1)], fail_jobs=True)
        with pytest.raises(RateLimited):
            await ingest_repo(provider, conn, "acme", "widget", limit=10)

        etag = conn.execute("SELECT runs_etag FROM repo WHERE id=%s", (REPO.id,)).fetchone()[0]
        assert etag is None, "ETag advanced despite jobs never being written"

    async def test_etag_is_stored_after_a_clean_pass(self, conn):
        provider = FakeProvider([_run(1)])
        await ingest_repo(provider, conn, "acme", "widget", limit=10)
        etag = conn.execute("SELECT runs_etag FROM repo WHERE id=%s", (REPO.id,)).fetchone()[0]
        assert etag == 'W/"v1"'

    async def test_a_retry_after_failure_refetches_rather_than_304ing(self, conn):
        failing = FakeProvider([_run(1)], fail_jobs=True)
        with pytest.raises(RateLimited):
            await ingest_repo(failing, conn, "acme", "widget", limit=10)

        # Second pass, same repo, now healthy: it must actually re-read the runs list
        # instead of short-circuiting on a stored ETag.
        healthy = FakeProvider([_run(1)])
        stats = await ingest_repo(healthy, conn, "acme", "widget", limit=10)
        assert not stats.not_modified
        assert stats.jobs_written == 1


class TestRerunAttempts:
    """filter=latest hides a re-run's earlier attempts, which are the strongest flaky
    label available. Verified against live data: a 3-attempt django-rest-framework run
    had 4 failing jobs on attempt 1 that were never ingested."""

    async def test_earlier_attempts_are_fetched_for_reruns(self, conn):
        provider = FakeProvider([_run(1, attempt=3)])
        await ingest_repo(provider, conn, "acme", "widget", limit=10)

        assert (1, None) in provider.jobs_calls  # latest
        assert (1, 1) in provider.jobs_calls  # the failing attempts
        assert (1, 2) in provider.jobs_calls

        rows = conn.execute("SELECT attempt FROM job WHERE run_id=1 ORDER BY attempt").fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]

    async def test_single_attempt_runs_cost_no_extra_calls(self, conn):
        """Reruns are ~0.6% of runs; the extra fetches must not apply to the other 99%."""
        provider = FakeProvider([_run(1, attempt=1)])
        await ingest_repo(provider, conn, "acme", "widget", limit=10)
        assert provider.jobs_calls == [(1, None)]


class TestJobNamePreservation:
    async def test_verbatim_name_and_base_are_both_stored(self, conn):
        run = _run(1)
        provider = FakeProvider([run])
        await ingest_repo(provider, conn, "acme", "widget", limit=10)
        conn.execute(
            "UPDATE job SET name='deploy (staging)', name_base='deploy' WHERE run_id=1"
        )
        conn.commit()
        row = conn.execute("SELECT name, name_base FROM job WHERE run_id=1").fetchone()
        assert row[0] == "deploy (staging)"
        assert row[1] == "deploy"


class TestStepsAreWritten:
    def test_steps_land_with_their_job(self, conn):
        conn.execute(
            "INSERT INTO repo (id, owner, name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (REPO.id, REPO.owner, REPO.name),
        )
        upsert_run(conn, _run())
        job = _job(
            910,
            started_at=T0,
            steps=[
                Step(1, "Checkout", "completed", "success", T0, T0 + timedelta(seconds=10)),
                Step(
                    2, "npm ci", "completed", "success",
                    T0 + timedelta(seconds=10), T0 + timedelta(seconds=300),
                ),
            ],
        )
        written = upsert_job(conn, job, REPO.id)
        conn.commit()
        assert written == 2
        n = conn.execute("SELECT count(*) FROM step WHERE job_id=910").fetchone()[0]
        assert n == 2
