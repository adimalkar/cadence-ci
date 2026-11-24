"""Backtests: fetch a past date range, and audit history as it stood at a past instant.

Against a real Postgres, like the ingest tests. The properties pinned here are the ones a
backtest is worthless without: the delta-poll cursor is never disturbed, a resumed
backfill does not pay twice, nothing after `as_of` leaks into the analysis, and GitHub's
1,000-run cap on filtered listings cannot silently truncate a busy day.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import typer

from cadence import ingest
from cadence.audit import build_context
from cadence.cli import _parse_instant
from cadence.db.conn import apply_migrations
from cadence.ingest import BackfillStats, backfill_window, upsert_job, upsert_repo, upsert_run
from cadence.models import Job, Repo, Run, RunPage
from cadence.providers.base import RateLimited

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


db = pytest.mark.skipif(not _db_available(), reason="no test database")

REPO = Repo(id=5151, owner="acme", name="backtest", is_private=True, default_branch="main")
DAY = datetime(2026, 8, 10, tzinfo=UTC)
YAML = {".github/workflows/ci.yml": (
    "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n")}


@pytest.fixture(scope="module")
def schema():
    if _db_available():
        apply_migrations(TEST_DB)


@pytest.fixture
def conn(schema):
    from psycopg.rows import dict_row
    with psycopg.connect(TEST_DB, row_factory=dict_row) as c:
        yield c
        c.rollback()
        c.execute("DELETE FROM job WHERE repo_id = %s", (REPO.id,))
        c.execute("DELETE FROM run WHERE repo_id = %s", (REPO.id,))
        c.execute("DELETE FROM repo WHERE id = %s", (REPO.id,))
        c.commit()


def _run(run_id: int, created: datetime, **over) -> Run:
    base = dict(
        id=run_id, repo_id=REPO.id, workflow_id=7, workflow_path=".github/workflows/ci.yml",
        workflow_name="CI", run_number=run_id, run_attempt=1, event="push",
        status="completed", conclusion="success", head_sha=f"sha{run_id}", head_branch="main",
        created_at=created, started_at=created, updated_at=created,
    )
    base.update(over)
    return Run(**base)


def _job(run: Run, labels=("ubuntu-latest",)) -> Job:
    return Job(
        id=run.id * 10, run_id=run.id, name="test", name_base="test", status="completed",
        conclusion="success", runner_labels=list(labels), runner_group=None,
        created_at=run.created_at, started_at=run.created_at + timedelta(seconds=5),
        completed_at=run.created_at + timedelta(minutes=3), attempt=1, steps=[],
    )


def _window(created: str) -> tuple[datetime, datetime]:
    a, b = created.split("..")
    return (datetime.fromisoformat(a.replace("Z", "+00:00")),
            datetime.fromisoformat(b.replace("Z", "+00:00")))


class FakeProvider:
    """Serves runs by GitHub's `created` filter, newest first, with its 1,000-run cap."""

    name = "fake"

    def __init__(self, runs: list[Run], *, fail_jobs_for: set[int] = frozenset()):
        self.runs = runs
        self.fail_jobs_for = fail_jobs_for
        self.jobs_calls: list[int] = []
        self.listings: list[str] = []

    async def fetch_runs(self, repo, *, page=1, per_page=100, etag=None, created=None):
        assert etag is None, "a backfill must never send the delta-poll ETag"
        assert created is not None
        self.listings.append(created)
        lo, hi = _window(created)
        hits = sorted((r for r in self.runs if lo <= r.created_at <= hi),
                      key=lambda r: r.created_at, reverse=True)[:ingest.FILTERED_LISTING_CAP]
        return RunPage(runs=hits[(page - 1) * per_page: page * per_page], etag='W/"new"')

    async def fetch_jobs(self, repo, run_id: int, *, attempt=None) -> list[Job]:
        self.jobs_calls.append(run_id)
        if run_id in self.fail_jobs_for:
            raise RateLimited(30.0, "simulated")
        run = next(r for r in self.runs if r.id == run_id)
        return [_job(run)]


def _stored(conn) -> dict[int, int]:
    """run id -> stored job count."""
    rows = conn.execute(
        "SELECT r.id, count(j.id) AS n FROM run r LEFT JOIN job j ON j.run_id = r.id "
        "WHERE r.repo_id = %s GROUP BY r.id", (REPO.id,)).fetchall()
    return {row["id"]: row["n"] for row in rows}


def _day(n: int = 0) -> tuple[datetime, datetime]:
    start = DAY + timedelta(days=n)
    return start, start + timedelta(days=1) - timedelta(seconds=1)


@db
class TestBackfillWindow:
    async def test_stores_the_window_with_jobs_and_nothing_outside_it(self, conn):
        inside = [_run(i, DAY + timedelta(hours=i)) for i in range(1, 4)]
        outside = [_run(9, DAY - timedelta(minutes=1)), _run(10, DAY + timedelta(days=1))]
        stats = await backfill_window(FakeProvider(inside + outside), conn, REPO, *_day())
        assert _stored(conn) == {1: 1, 2: 1, 3: 1}
        assert (stats.runs_written, stats.jobs_written) == (3, 3)

    async def test_the_delta_poll_cursor_is_never_touched(self, conn):
        upsert_repo(conn, REPO)
        conn.execute("UPDATE repo SET runs_etag = 'W/\"poll\"' WHERE id = %s", (REPO.id,))
        conn.commit()
        await backfill_window(FakeProvider([_run(1, DAY)]), conn, REPO, *_day())
        etag = conn.execute("SELECT runs_etag FROM repo WHERE id = %s", (REPO.id,)).fetchone()
        assert etag["runs_etag"] == 'W/"poll"'

    async def test_a_resumed_backfill_does_not_refetch_stored_jobs(self, conn):
        runs = [_run(i, DAY + timedelta(hours=i)) for i in range(1, 4)]
        await backfill_window(FakeProvider(runs), conn, REPO, *_day())
        again = FakeProvider(runs)
        stats = await backfill_window(again, conn, REPO, *_day())
        assert again.jobs_calls == []
        assert stats.runs_skipped_known == 3

    async def test_a_run_stored_while_in_progress_is_fetched_again(self, conn):
        upsert_repo(conn, REPO)
        live = _run(1, DAY, status="in_progress", conclusion=None)
        upsert_run(conn, live)
        upsert_job(conn, _job(live), REPO.id)
        conn.commit()
        provider = FakeProvider([_run(1, DAY)])
        await backfill_window(provider, conn, REPO, *_day())
        assert provider.jobs_calls == [1]

    async def test_a_failure_leaves_the_window_unwritten(self, conn):
        runs = [_run(i, DAY + timedelta(hours=i)) for i in range(1, 4)]
        with pytest.raises(RateLimited):
            await backfill_window(FakeProvider(runs, fail_jobs_for={2}), conn, REPO, *_day())
        conn.rollback()
        assert _stored(conn) == {}

    async def test_a_window_at_the_listing_cap_is_split_not_truncated(self, conn, monkeypatch):
        monkeypatch.setattr(ingest, "FILTERED_LISTING_CAP", 200)
        runs = [_run(i, DAY + timedelta(minutes=5 * i)) for i in range(1, 251)]
        provider = FakeProvider(runs)
        stats = await backfill_window(provider, conn, REPO, *_day())
        assert len(_stored(conn)) == 250
        assert stats.windows_split >= 1
        assert len(provider.jobs_calls) == 250  # each run fetched once, not per pass

    async def test_an_unsplittable_window_refuses_rather_than_truncates(self, conn, monkeypatch):
        monkeypatch.setattr(ingest, "FILTERED_LISTING_CAP", 100)
        runs = [_run(i, DAY) for i in range(1, 151)]
        with pytest.raises(RuntimeError, match="cannot split further"):
            await backfill_window(FakeProvider(runs), conn, REPO, *_day())

    def test_stats_render_the_backfill_counters(self):
        stats = BackfillStats(runs_written=3, runs_skipped_known=2, windows_split=1)
        assert "2 already stored, 1 splits" in str(stats)


@db
class TestAsOf:
    def _seed(self, conn):
        upsert_repo(conn, REPO)
        # Hosted until the 20th, then a move to self-hosted -- the shape that makes the
        # dominant runner label depend on when you ask.
        for i in range(1, 31):
            created = DAY + timedelta(days=i - 1)
            run = _run(i, created)
            upsert_run(conn, run)
            labels = ("ubuntu-latest",) if i < 20 else ("self-hosted", "linux")
            upsert_job(conn, _job(run, labels), REPO.id)
        conn.commit()

    def test_nothing_created_after_as_of_is_read(self, conn):
        self._seed(conn)
        as_of = DAY + timedelta(days=9, hours=12)
        ctx = build_context(conn, REPO.id, YAML, window_days=90, as_of=as_of)
        assert sorted(r.run_id for r in ctx.runs) == list(range(1, 11))

    def test_the_window_ends_at_as_of(self, conn):
        self._seed(conn)
        ctx = build_context(conn, REPO.id, YAML, window_days=3,
                            as_of=DAY + timedelta(days=9, hours=12))
        assert sorted(r.run_id for r in ctx.runs) == [8, 9, 10]

    def test_runner_labels_are_those_in_use_at_the_time(self, conn):
        self._seed(conn)
        before = build_context(conn, REPO.id, YAML, as_of=DAY + timedelta(days=15))
        assert before.cost.dominant_labels == ["ubuntu-latest"]

    def test_without_as_of_the_window_ends_now(self, conn):
        self._seed(conn)
        ctx = build_context(conn, REPO.id, YAML, window_days=36500)
        assert len(ctx.runs) == 30


class TestParseInstant:
    def test_a_date_means_its_start_in_utc(self):
        assert _parse_instant("2026-08-01") == datetime(2026, 8, 1, tzinfo=UTC)

    def test_an_iso_instant_keeps_its_offset(self):
        got = _parse_instant("2026-08-01T12:00:00+05:30")
        assert got == datetime(2026, 8, 1, 6, 30, tzinfo=UTC)

    def test_a_naive_instant_is_utc(self):
        assert _parse_instant("2026-08-01T12:00:00") == datetime(2026, 8, 1, 12, tzinfo=UTC)

    def test_z_suffix(self):
        assert _parse_instant("2026-08-01T12:00:00Z") == datetime(2026, 8, 1, 12, tzinfo=UTC)

    def test_garbage_is_a_parameter_error(self):
        with pytest.raises(typer.BadParameter):
            _parse_instant("last tuesday")


@db
class TestStepCoverage:
    def test_counts_runs_with_any_step_timing(self, conn):
        from cadence.audit import runs_with_steps
        from cadence.models import Step

        upsert_repo(conn, REPO)
        for i, with_steps in [(1, True), (2, False), (3, True)]:
            run = _run(i, DAY)
            upsert_run(conn, run)
            job = _job(run)
            if with_steps:
                job.steps = [Step(number=1, name="pytest", status="completed",
                                  conclusion="success", started_at=run.created_at,
                                  completed_at=run.created_at + timedelta(seconds=30))]
            upsert_job(conn, job, REPO.id)
        conn.commit()
        assert runs_with_steps(conn, [1, 2, 3]) == 2
        assert runs_with_steps(conn, []) == 0
