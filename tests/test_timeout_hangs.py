"""Jobs killed by their own timeout, which GitHub records as `cancelled`.

Shape from a backtest on a private field repository: an install step hung until a
35-minute job timeout, each kill 18-24 s past the limit, each re-run passing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, CancelledJob
from cadence.detectors.timeouts import (
    DETECTOR_VERSION,
    TimeoutHangDetector,
    group_hangs,
    killed_by_timeout,
)
from cadence.workflow import parse_workflow

E2E = ".github/workflows/e2e.yml"
YAML = """\
on: [push, pull_request]
jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 35
    steps:
      - name: Install browser
        run: npx playwright install --with-deps chromium
      - name: Test
        run: npx playwright test
"""


def ctx(cancelled: list[CancelledJob], yaml: str = YAML) -> AuditContext:
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=[parse_workflow(E2E, yaml)], runs=[], step_series={},
        cost=CostContext(is_private=False, runs_per_month=100.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=30, cancelled_jobs=cancelled,
    )


def kill(run_id: int, *, seconds: float = 35 * 60 + 20, step: str | None = "Install browser",
         attempt: int = 1, run_attempt: int = 2, run_conclusion: str | None = "success",
         job: str = "e2e") -> CancelledJob:
    return CancelledJob(run_id=run_id, attempt=attempt, workflow_path=E2E, job_name=job,
                        name_base=job, exec_seconds=seconds, step_name=step,
                        step_seconds=None if step is None else seconds - 60,
                        run_attempt=run_attempt, run_conclusion=run_conclusion)


class TestFinding:
    def test_hangs_at_the_timeout_are_reported_with_the_running_step(self):
        finding, = TimeoutHangDetector().run(ctx([kill(i) for i in range(1, 5)]))
        assert finding.title == (
            "`Install browser` in `e2e` (.github/workflows/e2e.yml) hung until the "
            "35-minute job timeout 4 times (141 runner-minutes); 4 of 4 runs passed on re-run")
        assert finding.module == "flake"
        assert finding.detector_version == DETECTOR_VERSION
        assert finding.severity == 3
        assert finding.savings is None
        config, history = finding.evidence
        assert (config.file_path, config.line_start) == (E2E, 3)
        assert config.payload == {"job": "e2e", "timeout_minutes": 35.0,
                                  "timeout_declared": True}
        assert history.run_ids == [1, 2, 3, 4]
        assert history.payload["runs_passed_on_rerun"] == 4
        assert "own `timeout-minutes`" in finding.suggested_action
        assert "environment" in finding.suggested_action

    def test_many_kills_raise_severity(self):
        finding, = TimeoutHangDetector().run(ctx([kill(i) for i in range(1, 6)]))
        assert finding.severity == 4

    def test_a_run_killed_on_two_attempts_counts_both_kills_once_as_a_run(self):
        kills = [kill(7, attempt=1, run_attempt=3), kill(7, attempt=2, run_attempt=3)]
        finding, = TimeoutHangDetector().run(ctx(kills))
        assert "2 times" in finding.title
        assert "1 of 1 runs passed on re-run" in finding.title

    def test_kills_without_a_passing_rerun_do_not_blame_the_environment(self):
        kills = [kill(i, run_attempt=1, run_conclusion="cancelled") for i in range(1, 4)]
        finding, = TimeoutHangDetector().run(ctx(kills))
        assert "passed on re-run" not in finding.title
        assert "environment" not in finding.suggested_action

    def test_the_default_timeout_applies_when_none_is_declared(self):
        yaml = YAML.replace("    timeout-minutes: 35\n", "")
        kills = [kill(i, seconds=360 * 60 + 40) for i in range(1, 3)]
        finding, = TimeoutHangDetector().run(ctx(kills, yaml))
        assert "360-minute job timeout" in finding.title
        assert "360-minute default" in finding.suggested_action
        assert finding.evidence[0].payload["timeout_declared"] is False

    def test_each_step_is_its_own_finding(self):
        kills = [kill(i) for i in range(1, 4)] + [kill(i, step="Test") for i in range(10, 13)]
        assert [g.step for g in group_hangs(ctx(kills))] == ["Install browser", "Test"]
        assert len(TimeoutHangDetector().run(ctx(kills))) == 2


class TestWhatIsNotAHang:
    def test_a_single_kill_stays_quiet(self):
        assert TimeoutHangDetector().run(ctx([kill(1)])) == []

    def test_cancelled_well_before_the_limit_is_someone_else(self):
        kills = [kill(i, seconds=10 * 60) for i in range(1, 6)]
        assert TimeoutHangDetector().run(ctx(kills)) == []

    def test_an_expression_timeout_is_not_guessed(self):
        yaml = YAML.replace("timeout-minutes: 35", "timeout-minutes: ${{ inputs.minutes }}")
        assert TimeoutHangDetector().run(ctx([kill(i) for i in range(1, 5)], yaml)) == []

    def test_a_job_that_does_not_resolve_is_skipped(self):
        kills = [kill(i, job="elsewhere") for i in range(1, 5)]
        assert TimeoutHangDetector().run(ctx(kills)) == []


class TestTimeoutMath:
    @pytest.mark.parametrize("seconds,killed", [
        (35 * 60 - 31, False), (35 * 60 - 30, True), (35 * 60 + 24, True),
        (35 * 60 + 300, True), (35 * 60 + 301, False),
    ])
    def test_tolerance(self, seconds, killed):
        assert killed_by_timeout(seconds, 35.0) is killed

    @pytest.mark.parametrize("value,expected", [
        ("35", 35.0), ("'35'", 35.0), ("2.5", 2.5), ("true", None),
        ("${{ inputs.minutes }}", None),
    ])
    def test_timeout_minutes_parsing(self, value, expected):
        yaml = YAML.replace("timeout-minutes: 35", f"timeout-minutes: {value}")
        assert parse_workflow(E2E, yaml).jobs["e2e"].timeout_minutes == expected

    def test_absent_means_githubs_default(self):
        yaml = YAML.replace("    timeout-minutes: 35\n", "")
        assert parse_workflow(E2E, yaml).jobs["e2e"].timeout_minutes == 360.0


TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="no test database")
def test_build_context_collects_every_cancelled_attempt_with_its_running_step():
    from psycopg.rows import dict_row

    from cadence.audit import build_context
    from cadence.db.conn import apply_migrations
    from cadence.ingest import upsert_job, upsert_repo, upsert_run
    from cadence.models import Job, Repo, Run, Step

    apply_migrations(TEST_DB)
    repo = Repo(id=7171, owner="acme", name="hangs", is_private=False, default_branch="main")
    t0 = datetime.now(UTC) - timedelta(hours=3)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        try:
            upsert_repo(conn, repo)
            upsert_run(conn, Run(
                id=71, repo_id=repo.id, workflow_id=1, workflow_path=E2E, workflow_name="E2E",
                run_number=1, run_attempt=2, event="push", status="completed",
                conclusion="success", head_sha="abc", head_branch="main",
                created_at=t0, started_at=t0, updated_at=t0 + timedelta(hours=1),
            ))

            def step(n, name, conclusion, start, minutes):
                return Step(number=n, name=name, status="completed", conclusion=conclusion,
                            started_at=start,
                            completed_at=None if minutes is None
                            else start + timedelta(minutes=minutes))

            killed = Job(
                id=711, run_id=71, name="e2e", name_base="e2e", status="completed",
                conclusion="cancelled", runner_labels=["ubuntu-latest"], runner_group=None,
                created_at=t0, started_at=t0, completed_at=t0 + timedelta(seconds=2120),
                attempt=1, steps=[step(1, "Checkout", "success", t0, 0.2),
                                  step(2, "Install browser", "cancelled",
                                       t0 + timedelta(seconds=30), 34.5),
                                  step(3, "Test", "cancelled", None, None)])
            passed = Job(
                id=712, run_id=71, name="e2e", name_base="e2e", status="completed",
                conclusion="success", runner_labels=["ubuntu-latest"], runner_group=None,
                created_at=t0 + timedelta(minutes=40), started_at=t0 + timedelta(minutes=40),
                completed_at=t0 + timedelta(minutes=52), attempt=2, steps=[])
            upsert_job(conn, killed, repo.id)
            upsert_job(conn, passed, repo.id)
            conn.commit()

            built = build_context(conn, repo.id, {E2E: YAML}, window_days=1)
            c, = built.cancelled_jobs
            assert (c.run_id, c.attempt, c.run_attempt, c.run_conclusion) == (71, 1, 2, "success")
            assert c.step_name == "Install browser"
            assert c.exec_seconds == pytest.approx(2120.0)
        finally:
            conn.rollback()
            conn.execute("DELETE FROM job WHERE repo_id = %s", (repo.id,))
            conn.execute("DELETE FROM run WHERE repo_id = %s", (repo.id,))
            conn.execute("DELETE FROM repo WHERE id = %s", (repo.id,))
            conn.commit()
