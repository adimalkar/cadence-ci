"""runner_queue_wait: tail waits before a run starts, per triggering event."""

from __future__ import annotations

import pytest

from cadence.audit import _runner_kind
from cadence.cost import CostContext, RateCard
from cadence.dag import NodeTiming
from cadence.detectors.context import AuditContext, RunObservation
from cadence.detectors.queue import (
    MIN_RUNS,
    RunnerQueueWaitDetector,
    percentile,
    profile_waits,
)
from cadence.workflow import parse_workflow

CI = """\
on: [push, pull_request]
jobs:
  build:
    runs-on: [self-hosted, linux]
    steps:
      - run: make
"""

DEPLOY = """\
on: push
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ./deploy
"""

HOUR = 3600.0


def ctx_for(workflows: dict[str, str], runs: list[RunObservation]) -> AuditContext:
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=True,
        workflows=[parse_workflow(p, y) for p, y in workflows.items()],
        runs=runs, step_series={}, step_series_resolved={},
        cost=CostContext(
            is_private=True, runs_per_month=200.0,
            rate_card=RateCard(version=1, rates={}, free_on_public={}),
        ),
        window_days=90,
    )


_ids = iter(range(1, 1_000_000))


def run(wait: float | None, *, event: str = "push", path: str = "ci.yml",
        kind: str | None = "self-hosted") -> RunObservation:
    queued = 1_000.0
    return RunObservation(
        run_id=next(_ids), head_branch="main", started_epoch=queued,
        exec_started_epoch=None if wait is None else queued + wait,
        completed_epoch=queued + (wait or 0) + 600, conclusion="success",
        workflow_path=path, event=event, runner_kind=kind,
        timings={"build": NodeTiming("build", wait or 0, 600)},
    )


def tail(n: int, slow: int, wait: float, **kw) -> list[RunObservation]:
    """`n` runs, `slow` of which waited `wait` seconds; the rest started at once."""
    return [run(wait if i < slow else 0.0, **kw) for i in range(n)]


class TestTheMedianHidesTheTail:
    """The field case: a median wait of zero and a p90 of hours on one event."""

    def test_a_long_tail_fires_although_the_median_is_zero(self):
        runs = tail(25, slow=6, wait=2 * HOUR)
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs))
        payload = d.evidence[0].payload
        assert payload["wait_p50_seconds"] == 0.0
        assert payload["wait_p90_seconds"] == 2 * HOUR
        assert d.title.startswith("`push` runs wait p90 120:00")
        assert d.savings is None
        assert d.severity == 3

    def test_the_self_hosted_advice_names_the_pool(self):
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, tail(25, 6, HOUR)))
        assert "self-hosted runner" in d.suggested_action
        assert d.evidence[0].payload["runner_kind"] == "self-hosted"

    def test_hosted_runs_get_hosted_advice(self):
        runs = tail(25, 6, HOUR, kind="hosted")
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs))
        assert "GitHub-hosted" in d.suggested_action

    def test_evidence_cites_the_worst_runs_first(self):
        runs = tail(25, 6, HOUR)
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs))
        slow_ids = {r.run_id for r in runs[:6]}
        assert set(d.evidence[0].run_ids[:6]) == slow_ids


class TestThresholds:
    def test_a_short_tail_is_silent(self):
        runs = tail(25, 6, 9 * 60)
        assert RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs)) == []

    def test_a_few_outliers_do_not_make_a_p90(self):
        runs = tail(30, 2, 3 * HOUR)
        assert RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs)) == []

    def test_too_few_runs_is_silent(self):
        runs = tail(MIN_RUNS - 1, MIN_RUNS - 1, 3 * HOUR)
        assert RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs)) == []

    def test_moderate_waits_are_lower_severity(self):
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, tail(25, 6, 20 * 60)))
        assert d.severity == 2


class TestStreams:
    def test_events_are_measured_apart(self):
        runs = tail(25, 6, HOUR, event="push") + tail(25, 0, 0, event="pull_request")
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs))
        assert d.dedupe_key == "runner_queue_wait:push"

    def test_workflows_sharing_an_event_are_one_finding(self):
        """A queue belongs to the runner pool. One saturated pool on a public corpus repo
        otherwise produced six findings with the same cause."""
        runs = tail(25, 6, HOUR, path="ci.yml") + tail(25, 6, HOUR, path="e2e.yml")
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI, "e2e.yml": CI}, runs))
        assert "ci.yml, e2e.yml" in d.title
        assert [w["workflow"] for w in d.evidence[0].payload["workflows"]] == [
            "ci.yml", "e2e.yml",
        ]
        assert d.evidence[0].payload["runs"] == 50

    def test_each_workflow_must_qualify_on_its_own(self):
        runs = tail(25, 6, HOUR, path="ci.yml") + tail(25, 0, 0, path="e2e.yml")
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI, "e2e.yml": CI}, runs))
        assert d.title.endswith("in ci.yml")
        assert d.evidence[0].payload["runs"] == 25

    def test_mixed_runner_kinds_are_named_as_mixed(self):
        runs = tail(25, 6, HOUR, path="ci.yml") + tail(25, 6, HOUR, path="e2e.yml",
                                                      kind="hosted")
        (d,) = RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI, "e2e.yml": CI}, runs))
        assert d.evidence[0].payload["runner_kind"] == "mixed"


class TestWhatIsNotAQueue:
    def test_an_approval_gated_workflow_is_skipped(self):
        """A deployment waiting for a reviewer is not waiting for a runner."""
        runs = tail(25, 25, 5 * HOUR, path="deploy.yml")
        assert RunnerQueueWaitDetector().run(ctx_for({"deploy.yml": DEPLOY}, runs)) == []

    def test_runs_from_workflows_not_in_config_are_skipped(self):
        runs = tail(25, 25, 5 * HOUR, path="dynamic/codeql")
        assert RunnerQueueWaitDetector().run(ctx_for({"ci.yml": CI}, runs)) == []

    def test_negative_and_implausible_waits_are_excluded_and_counted(self):
        runs = tail(25, 6, HOUR) + [run(-30.0), run(3 * 24 * HOUR), run(None)]
        (prof,) = profile_waits(ctx_for({"ci.yml": CI}, runs))
        assert len(prof.waits) == 25
        assert prof.excluded == 2

    def test_runs_without_an_event_are_skipped(self):
        runs = tail(25, 25, HOUR, event=None)  # type: ignore[arg-type]
        assert profile_waits(ctx_for({"ci.yml": CI}, runs)) == []


class TestHelpers:
    @pytest.mark.parametrize(("q", "expected"), [(0.5, 5), (0.9, 9), (1.0, 10), (0.01, 1)])
    def test_percentile_is_nearest_rank(self, q, expected):
        assert percentile([float(v) for v in range(1, 11)], q) == expected

    def test_percentile_of_nothing_is_an_error(self):
        with pytest.raises(ValueError):
            percentile([], 0.9)

    @pytest.mark.parametrize(("labels", "expected"), [
        ([["self-hosted", "linux", "x64"]], "self-hosted"),
        ([["ubuntu-latest"]], "hosted"),
        ([["Self-Hosted"], ["ubuntu-latest"]], "mixed"),
        ([None, []], None),
    ])
    def test_runner_kind_reads_the_self_hosted_label(self, labels, expected):
        assert _runner_kind([{"runner_labels": lb} for lb in labels]) == expected
