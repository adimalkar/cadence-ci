"""Default-branch commits whose every CI run was displaced by the next push.

The shape comes from a backtest on a private field repository: a group keyed on
`github.ref` with `cancel-in-progress: true` dropped 22 commits on main over 18 days.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, RunOutcome
from cadence.detectors.untested import (
    DETECTOR_VERSION,
    UntestedDefaultBranchCommitsDetector,
    group_is_shared,
    untested_commits,
)
from cadence.workflow import parse_workflow

CI = ".github/workflows/ci.yml"
SHARED = """\
name: CI
on: [push, pull_request]

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make test
"""
PER_COMMIT = SHARED.replace("${{ github.ref }}",
                            "${{ github.event.pull_request.number || github.sha }}")


def ctx(yaml: str, outcomes: list[RunOutcome], branch: str | None = "main") -> AuditContext:
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=[parse_workflow(CI, yaml)], runs=[], step_series={},
        cost=CostContext(is_private=False, runs_per_month=100.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=30, default_branch=branch, run_outcomes=outcomes,
    )


def run(run_id: int, sha: str, t: float, conclusion: str | None, *, ended: float | None = None,
        event: str = "push", branch: str = "main", path: str = CI) -> RunOutcome:
    return RunOutcome(run_id=run_id, workflow_path=path, event=event, head_branch=branch,
                      head_sha=sha, conclusion=conclusion, created_epoch=t,
                      updated_epoch=ended if ended is not None else t + 300)


def burst(n: int, *, start: int = 0) -> list[RunOutcome]:
    """n pushes a minute apart; each displaced by the next, the last one completes."""
    out = []
    for i in range(n):
        t = 1000.0 + 60 * (start + i)
        last = i == n - 1
        out.append(run(start + i + 1, f"sha{start + i}", t,
                       "failure" if last else "cancelled",
                       ended=t + 600 if last else t + 61))
    return out


class TestFinding:
    def test_displaced_pushes_on_the_default_branch_are_reported(self):
        finding, = UntestedDefaultBranchCommitsDetector().run(ctx(SHARED, burst(5)))
        assert finding.title.startswith("4 of 5 commits pushed to `main` never got a result")
        assert finding.module == "observability"
        assert finding.detector_version == DETECTOR_VERSION
        assert finding.savings is None
        assert finding.severity == 3
        config, history = finding.evidence
        assert (config.file_path, config.line_start) == (CI, 4)  # the `concurrency:` line
        assert config.payload["cancel-in-progress"] is True
        assert history.run_ids == [1, 2, 3, 4]
        assert history.payload["untested"][0] == {
            "sha": "sha0", "cancelled_runs": [1], "displaced_by_run": 2}
        assert "cancels the running one" in finding.suggested_action
        assert "github.sha" in finding.suggested_action

    def test_many_untested_commits_raise_severity(self):
        finding, = UntestedDefaultBranchCommitsDetector().run(ctx(SHARED, burst(11)))
        assert finding.severity == 4

    def test_too_few_stay_quiet(self):
        assert UntestedDefaultBranchCommitsDetector().run(ctx(SHARED, burst(3))) == []

    def test_without_cancel_in_progress_pending_runs_are_still_replaced(self):
        yaml = SHARED.replace("  cancel-in-progress: true\n", "")
        finding, = UntestedDefaultBranchCommitsDetector().run(ctx(yaml, burst(5)))
        assert "replaces the pending one" in finding.suggested_action

    def test_a_conditional_cancel_is_described_as_possible(self):
        yaml = SHARED.replace("cancel-in-progress: true",
                              "cancel-in-progress: ${{ github.event_name == 'push' }}")
        finding, = UntestedDefaultBranchCommitsDetector().run(ctx(yaml, burst(5)))
        assert "may cancel the running one" in finding.suggested_action


class TestWhatIsNotUntested:
    def test_a_per_commit_group_cannot_evict(self):
        assert UntestedDefaultBranchCommitsDetector().run(ctx(PER_COMMIT, burst(8))) == []

    def test_no_concurrency_means_no_eviction(self):
        yaml = SHARED.replace(
            "concurrency:\n  group: ci-${{ github.workflow }}-${{ github.ref }}\n"
            "  cancel-in-progress: true\n", "")
        assert UntestedDefaultBranchCommitsDetector().run(ctx(yaml, burst(8))) == []

    def test_a_rerun_that_concluded_means_the_commit_was_tested(self):
        runs = burst(5) + [run(99, "sha0", 5000.0, "success")]
        lost, considered = untested_commits(runs, CI, "main")
        assert [c.sha for c in lost] == ["sha1", "sha2", "sha3"]
        assert considered == 5

    def test_a_manual_cancel_with_no_successor_is_not_displacement(self):
        runs = [run(1, "a", 1000.0, "cancelled", ended=1100.0),
                run(2, "b", 5000.0, "success")]
        assert untested_commits(runs, CI, "main")[0] == []

    def test_a_run_still_in_progress_is_not_judged(self):
        runs = burst(4) + [run(50, "sha3", 2000.0, None)]
        assert "sha3" not in {c.sha for c in untested_commits(runs, CI, "main")[0]}

    @pytest.mark.parametrize("over", [{"event": "pull_request"}, {"branch": "feature"},
                                      {"path": ".github/workflows/other.yml"}])
    def test_only_this_workflows_pushes_to_the_default_branch_count(self, over):
        runs = [run(i + 1, f"s{i}", 1000.0 + 60 * i, "cancelled", ended=1061.0 + 60 * i, **over)
                for i in range(6)]
        assert untested_commits(runs, CI, "main") == ([], 0)

    def test_an_unknown_default_branch_stays_quiet(self):
        assert UntestedDefaultBranchCommitsDetector().run(ctx(SHARED, burst(8), None)) == []


class TestGroupIsShared:
    @pytest.mark.parametrize("group,shared", [
        ("ci-${{ github.ref }}", True),
        ("deploy", True),
        ("ci-${{ github.event.pull_request.number || github.sha }}", False),
        ("${{ github.workflow }}-${{ github.head_ref || github.run_id }}", False),
        ("", False),
    ])
    def test_group_keys(self, group, shared):
        yaml = SHARED.replace("ci-${{ github.workflow }}-${{ github.ref }}", group or '""')
        assert group_is_shared(parse_workflow(CI, yaml)) is shared

    def test_concurrency_line_is_recorded(self):
        assert parse_workflow(CI, SHARED).concurrency_line == 4
        assert parse_workflow(CI, "on: push\njobs: {}\n").concurrency_line is None


# --- build_context must hand the rule runs that never started a job -----------------

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="no test database")
def test_build_context_includes_runs_evicted_before_any_job_started():
    from psycopg.rows import dict_row

    from cadence.audit import build_context
    from cadence.db.conn import apply_migrations
    from cadence.ingest import upsert_repo, upsert_run
    from cadence.models import Repo, Run

    apply_migrations(TEST_DB)
    repo = Repo(id=6161, owner="acme", name="evicted", is_private=False, default_branch="trunk")
    now = datetime.now(UTC)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        try:
            upsert_repo(conn, repo)
            upsert_run(conn, Run(
                id=61, repo_id=repo.id, workflow_id=1, workflow_path=CI, workflow_name="CI",
                run_number=1, run_attempt=1, event="push", status="completed",
                conclusion="cancelled", head_sha="abc", head_branch="trunk",
                created_at=now - timedelta(hours=1), started_at=None,
                updated_at=now - timedelta(minutes=59),
            ))
            conn.commit()
            built = build_context(conn, repo.id, {CI: SHARED}, window_days=1)
            assert built.runs == []  # no job ever ran
            assert built.default_branch == "trunk"
            outcome, = built.run_outcomes
            assert (outcome.run_id, outcome.conclusion) == (61, "cancelled")
            assert outcome.head_sha == "abc"
            assert outcome.updated_epoch - outcome.created_epoch == pytest.approx(60.0)
        finally:
            conn.rollback()
            conn.execute("DELETE FROM run WHERE repo_id = %s", (repo.id,))
            conn.execute("DELETE FROM repo WHERE id = %s", (repo.id,))
            conn.commit()
