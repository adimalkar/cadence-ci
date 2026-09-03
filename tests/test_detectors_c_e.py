"""Catalog classes C–D: matrix legs, path triggers, long-tail steps.

These rules need more history than the current corpus holds per workflow, so their logic
is proven here against synthetic data rather than left unverified until ingest deepens.
"""

from __future__ import annotations

from cadence.cost import CostContext, RateCard
from cadence.dag import NodeTiming
from cadence.detectors.context import AuditContext, RunObservation, StepSeries
from cadence.detectors.longtail import LongTailStepDetector
from cadence.detectors.matrix import NonDiscriminatingMatrixLegDetector
from cadence.detectors.triggers import IrrelevantPathTriggerDetector, is_inert
from cadence.workflow import parse_workflow

RC = RateCard(version=2026, rates={"ubuntu-latest": 0.006},
              free_on_public={"ubuntu-latest": True})

MATRIX_WF = """\
name: CI
on: push
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - run: pytest
"""


def _ctx(workflow_yaml: str, path="ci.yml", **over) -> AuditContext:
    base = dict(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=[parse_workflow(path, workflow_yaml)], runs=[], step_series={},
        cost=CostContext(is_private=False, runs_per_month=200.0, rate_card=RC),
        window_days=90,
    )
    base.update(over)
    return AuditContext(**base)


def _runs(n: int, path="ci.yml") -> list[RunObservation]:
    return [
        RunObservation(
            run_id=i, head_branch="main", started_epoch=float(i * 1000),
            exec_started_epoch=float(i * 1000), completed_epoch=float(i * 1000 + 300),
            conclusion="success",
            workflow_path=path, jobs_total=3, jobs_mapped=3,
            timings={"test": NodeTiming("test", 0, 300)},
        )
        for i in range(n)
    ]


class TestNonDiscriminatingMatrixLeg:
    # Enough co-occurring failures to clear MIN_WORKFLOW_FAILURES — below that the
    # detector correctly declines to conclude anything about discrimination.
    CO_FAIL_RUNS = (10, 11, 12, 13, 14, 15)

    @classmethod
    def _legs(cls, sole_failure_for: str | None):
        """3 legs over 200 runs; failures always co-occur unless `sole_failure_for`."""
        legs = {}
        for leg in ("test (ubuntu-latest)", "test (windows-latest)", "test (macos-latest)"):
            outcomes = [(i, "success") for i in range(200)]
            # ubuntu + windows fail together, so neither is ever the sole failure
            if leg in ("test (ubuntu-latest)", "test (windows-latest)"):
                for i in cls.CO_FAIL_RUNS:
                    outcomes[i] = (i, "failure")
            if sole_failure_for == leg:
                outcomes[50] = (50, "failure")
            legs[leg] = outcomes
        return legs

    def _durations(self):
        return {
            ("ci.yml", leg): [120.0] * 60
            for leg in ("test (ubuntu-latest)", "test (windows-latest)",
                        "test (macos-latest)")
        }

    def test_flags_a_leg_that_never_failed_alone(self):
        ctx = _ctx(MATRIX_WF, runs=_runs(200),
                   leg_outcomes={"ci.yml": self._legs(None)},
                   leg_durations=self._durations())
        drafts = NonDiscriminatingMatrixLegDetector().run(ctx)
        kinds = {d.kind for d in drafts}
        assert kinds == {"non_discriminating_matrix_leg"}
        # macos never failed at all; ubuntu/windows only failed together -> all three
        # are non-discriminating.
        assert len(drafts) == 3
        assert drafts[0].savings.basis.is_replay

    def test_a_leg_that_caught_something_alone_is_spared(self):
        legs = self._legs("test (macos-latest)")
        ctx = _ctx(MATRIX_WF, runs=_runs(200), leg_outcomes={"ci.yml": legs},
                   leg_durations=self._durations())
        flagged = {d.dedupe_key for d in NonDiscriminatingMatrixLegDetector().run(ctx)}
        assert not any("macos" in k for k in flagged)
        assert any("windows" in k for k in flagged)

    def test_thin_history_produces_nothing(self):
        """Below the run threshold, 'never failed alone' is not evidence of anything."""
        ctx = _ctx(MATRIX_WF, runs=_runs(20),
                   leg_outcomes={"ci.yml": self._legs(None)},
                   leg_durations=self._durations())
        assert NonDiscriminatingMatrixLegDetector().run(ctx) == []

    def test_no_failures_at_all_produces_nothing(self):
        legs = {leg: [(i, "success") for i in range(200)]
                for leg in ("test (ubuntu-latest)", "test (windows-latest)")}
        ctx = _ctx(MATRIX_WF, runs=_runs(200), leg_outcomes={"ci.yml": legs},
                   leg_durations=self._durations())
        assert NonDiscriminatingMatrixLegDetector().run(ctx) == []

    def test_saving_is_billable_compute_not_wall_clock(self):
        """Removing one leg of a parallel matrix usually shortens nothing, so claiming
        wall-clock savings would be wrong."""
        ctx = _ctx(MATRIX_WF, runs=_runs(200),
                   leg_outcomes={"ci.yml": self._legs(None)},
                   leg_durations=self._durations())
        d = NonDiscriminatingMatrixLegDetector().run(ctx)[0]
        assert "compute, not wall clock" in d.savings.detail
        assert d.savings.seconds_per_run == 120.0


NO_FILTER = """\
name: CI
on:
  pull_request:
  push:
jobs:
  test:
    steps:
      - run: pytest
"""

WITH_FILTER = """\
name: CI
on:
  pull_request:
    paths-ignore: ['docs/**']
jobs:
  test:
    steps:
      - run: pytest
"""


class TestIrrelevantPathTrigger:
    def test_inert_classifier_is_conservative(self):
        assert is_inert("docs/guide.md")
        assert is_inert("README.md")
        assert is_inert("LICENSE")
        assert is_inert(".editorconfig")
        # Config, CI, and scripts genuinely can change behaviour.
        assert not is_inert("pyproject.toml")
        assert not is_inert(".github/workflows/ci.yml")
        assert not is_inert("scripts/build.sh")
        assert not is_inert("src/main.py")

    def test_fires_when_many_runs_were_docs_only(self):
        runs = _runs(40)
        changed = {r.run_id: (["docs/a.md"] if r.run_id % 4 == 0 else ["src/x.py"])
                   for r in runs}
        ctx = _ctx(NO_FILTER, runs=runs, changed_paths=changed)
        drafts = IrrelevantPathTriggerDetector().run(ctx)
        assert len(drafts) == 1
        assert drafts[0].savings.basis.is_replay  # those runs would not have started

    def test_a_mixed_changeset_justifies_the_run(self):
        runs = _runs(40)
        changed = {r.run_id: ["docs/a.md", "src/x.py"] for r in runs}
        ctx = _ctx(NO_FILTER, runs=runs, changed_paths=changed)
        assert IrrelevantPathTriggerDetector().run(ctx) == []

    def test_existing_path_filter_suppresses_the_finding(self):
        """The maintainer has clearly thought about it; second-guessing a hand-tuned
        filter is the kind of noise that gets a bot uninstalled."""
        runs = _runs(40)
        changed = {r.run_id: ["docs/a.md"] for r in runs}
        ctx = _ctx(WITH_FILTER, runs=runs, changed_paths=changed)
        assert IrrelevantPathTriggerDetector().run(ctx) == []

    def test_silent_without_changed_path_data(self):
        ctx = _ctx(NO_FILTER, runs=_runs(40))
        assert IrrelevantPathTriggerDetector().run(ctx) == []


class TestLongTailStep:
    def test_flags_a_dominant_step(self):
        series = {
            ("test", "pytest"): StepSeries("test", "pytest", [600.0] * 30, list(range(30))),
            ("test", "checkout"): StepSeries("test", "checkout", [5.0] * 30, list(range(30))),
        }
        ctx = _ctx(MATRIX_WF, step_series=series)
        drafts = LongTailStepDetector().run(ctx)
        assert len(drafts) == 1
        assert "pytest" in drafts[0].title

    def test_carries_no_savings_figure(self):
        """The fix is unspecified (shard? cache? nightly?), so any number would be a
        guess presented as a measurement."""
        series = {("test", "pytest"): StepSeries("test", "pytest", [600.0] * 30,
                                                 list(range(30)))}
        d = LongTailStepDetector().run(_ctx(MATRIX_WF, step_series=series))[0]
        assert d.savings is None

    def test_evenly_spread_steps_produce_nothing(self):
        series = {
            ("test", f"s{i}"): StepSeries("test", f"s{i}", [100.0] * 30, list(range(30)))
            for i in range(10)
        }
        assert LongTailStepDetector().run(_ctx(MATRIX_WF, step_series=series)) == []

    def test_fast_steps_ignored_even_if_dominant(self):
        series = {("test", "quick"): StepSeries("test", "quick", [3.0] * 30,
                                                list(range(30)))}
        assert LongTailStepDetector().run(_ctx(MATRIX_WF, step_series=series)) == []

    def test_flags_inconsistency_when_p95_far_exceeds_median(self):
        durations = [100.0] * 28 + [900.0, 950.0]
        series = {("test", "flaky-ish"): StepSeries("test", "flaky-ish", durations,
                                                    list(range(30)))}
        d = LongTailStepDetector().run(_ctx(MATRIX_WF, step_series=series))[0]
        assert "inconsistent" in d.suggested_action
