from __future__ import annotations

import pytest

from cadence.cost import CostContext, Currency, RateCard
from cadence.dag import NodeTiming
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.cache import DependencyCacheDetector
from cadence.detectors.cancellation import NoRunCancellationDetector
from cadence.detectors.context import AuditContext, RunObservation, StepSeries
from cadence.detectors.serialization import FalseNeedsEdgeDetector, independence_verdict
from cadence.workflow import parse_workflow

RATE_CARD = RateCard(
    version=2026,
    rates={"ubuntu-latest": 0.006, "ubuntu-latest-8-core": 0.024},
    free_on_public={"ubuntu-latest": True, "ubuntu-latest-8-core": False},
)


def make_ctx(workflow_yaml: str, *, runs=None, step_series=None, is_private=False,
             path="ci.yml") -> AuditContext:
    wf = parse_workflow(path, workflow_yaml)
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=is_private,
        workflows=[wf], runs=runs or [], step_series=step_series or {},
        cost=CostContext(is_private=is_private, runs_per_month=200.0, rate_card=RATE_CARD),
        window_days=90,
    )


def run_obs(run_id: int, branch: str, start: float, end: float, timings=None,
            workflow_path: str = "ci.yml", queue_seconds: float = 0.0) -> RunObservation:
    """`start` is queue entry; execution begins `queue_seconds` later.

    Defaulting the two to the same instant keeps every existing case unchanged while
    letting cancellation tests model a run that waited before it ran — which is the
    distinction that made a four-day queue wait read as four days of wasted compute.
    """
    return RunObservation(
        run_id=run_id, head_branch=branch, started_epoch=start,
        exec_started_epoch=start + queue_seconds, completed_epoch=end,
        conclusion="success", workflow_path=workflow_path,
        timings=timings or {"build": NodeTiming("build", 0, end - start)},
    )


# ───────────────────────────────────────────────────────── cancellation

NO_CONCURRENCY = """\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make
"""

WITH_CONCURRENCY = """\
name: CI
on: push
concurrency:
  group: ${{ github.ref }}
  cancel-in-progress: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make
"""


class TestCancellationDetector:
    def test_fires_when_runs_were_superseded(self):
        runs = [run_obs(1, "main", 0, 100), run_obs(2, "main", 40, 140)]
        ctx = make_ctx(NO_CONCURRENCY, runs=runs)
        drafts = NoRunCancellationDetector().run(ctx)
        assert len(drafts) == 1
        assert drafts[0].kind == "no_run_cancellation"
        assert drafts[0].savings.basis.is_replay

    def test_silent_when_concurrency_already_correct(self):
        runs = [run_obs(1, "main", 0, 100), run_obs(2, "main", 40, 140)]
        ctx = make_ctx(WITH_CONCURRENCY, runs=runs)
        assert NoRunCancellationDetector().run(ctx) == []

    def test_silent_when_nothing_was_superseded(self):
        runs = [run_obs(1, "main", 0, 50), run_obs(2, "main", 60, 100)]
        ctx = make_ctx(NO_CONCURRENCY, runs=runs)
        assert NoRunCancellationDetector().run(ctx) == []

    def test_carries_all_three_evidence_kinds(self):
        runs = [run_obs(1, "main", 0, 100), run_obs(2, "main", 40, 140)]
        drafts = NoRunCancellationDetector().run(make_ctx(NO_CONCURRENCY, runs=runs))
        kinds = {e.kind for e in drafts[0].evidence}
        assert kinds == {"code_range", "run_history", "counterfactual"}

    def test_waste_is_not_multiplied_across_workflows(self):
        """Regression: the detector once emitted the same repo-wide waste against every
        workflow file, so an 18-workflow repo reported ~18x the real figure and the
        replay total summed to nonsense. `concurrency` is per file, so each workflow may
        only be charged for its own runs."""
        ctx = AuditContext(
            repo_id=1, owner="acme", name="widget", is_private=False,
            workflows=[
                parse_workflow("ci.yml", NO_CONCURRENCY),
                parse_workflow("release.yml", NO_CONCURRENCY),
            ],
            # Only ci.yml ever had overlapping runs; release.yml ran cleanly.
            runs=[
                run_obs(1, "main", 0, 100, workflow_path="ci.yml"),
                run_obs(2, "main", 40, 140, workflow_path="ci.yml"),
                run_obs(3, "main", 500, 560, workflow_path="release.yml"),
            ],
            step_series={},
            cost=CostContext(is_private=False, runs_per_month=100.0, rate_card=RATE_CARD),
            window_days=90,
        )
        drafts = NoRunCancellationDetector().run(ctx)
        assert len(drafts) == 1
        assert "ci.yml" in drafts[0].title
        # 60s wasted over ci.yml's 2 runs -- not spread over all 3, and not doubled.
        assert drafts[0].savings.seconds_per_run == 30.0

    def test_workflow_with_no_runs_is_skipped(self):
        ctx = AuditContext(
            repo_id=1, owner="acme", name="widget", is_private=False,
            workflows=[parse_workflow("unused.yml", NO_CONCURRENCY)],
            runs=[run_obs(1, "main", 0, 100, workflow_path="other.yml"),
                  run_obs(2, "main", 40, 140, workflow_path="other.yml")],
            step_series={},
            cost=CostContext(is_private=False, runs_per_month=100.0, rate_card=RATE_CARD),
            window_days=90,
        )
        assert NoRunCancellationDetector().run(ctx) == []

    def test_billed_multiplier_reflects_concurrent_jobs(self):
        """A superseded run burns every job still executing, not one job's elapsed
        time -- so the dollar figure must scale with the run's job count."""
        timings = {k: NodeTiming(k, 0, 60) for k in ("a", "b", "c", "d")}
        runs = [run_obs(1, "main", 0, 100, timings), run_obs(2, "main", 40, 140, timings)]
        drafts = NoRunCancellationDetector().run(make_ctx(NO_CONCURRENCY, runs=runs))
        assert drafts[0].parallel_jobs == 4.0


# ───────────────────────────────────────────────────────── false needs edge

INDEPENDENT = """\
name: CI
on: push
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: ruff check .
  build:
    needs: [lint]
    runs-on: ubuntu-latest
    steps:
      - run: cargo build
"""


def _edge_runs(n=30):
    edges_timings = {"lint": NodeTiming("lint", 0, 30), "build": NodeTiming("build", 0, 200)}
    return [run_obs(i, "main", 0, 230, dict(edges_timings)) for i in range(n)]


class TestFalseNeedsEdge:
    def test_fires_on_a_genuinely_independent_edge(self):
        ctx = make_ctx(INDEPENDENT, runs=_edge_runs())
        drafts = FalseNeedsEdgeDetector().run(ctx)
        assert len(drafts) == 1
        assert drafts[0].kind == "false_needs_edge"
        assert drafts[0].savings.basis.is_replay

    def test_requires_a_minimum_run_count(self):
        """Below the threshold the replay median is noise from a handful of runs."""
        ctx = make_ctx(INDEPENDENT, runs=_edge_runs(5))
        assert FalseNeedsEdgeDetector().run(ctx) == []


class TestIndependenceVerdict:
    """The safety property: require positive evidence of independence. Anything
    unreadable must suppress the finding, because a wrong removal breaks a build."""

    @staticmethod
    def _jobs(yaml_text: str):
        wf = parse_workflow("ci.yml", yaml_text)
        return wf.jobs

    def test_output_reference_blocks(self):
        jobs = self._jobs("""\
jobs:
  a:
    outputs:
      v: ${{ steps.x.outputs.v }}
    steps: []
  b:
    needs: [a]
    env:
      VERSION: ${{ needs.a.outputs.v }}
    steps: []
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_declared_outputs_block_even_without_a_visible_reference(self):
        """The reference may live in a composite action we cannot read."""
        jobs = self._jobs("""\
jobs:
  a:
    outputs:
      v: "1"
    steps: []
  b:
    needs: [a]
    steps: []
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_artifact_flow_blocks(self):
        jobs = self._jobs("""\
jobs:
  a:
    steps:
      - uses: actions/upload-artifact@v4
  b:
    needs: [a]
    steps:
      - uses: actions/download-artifact@v4
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_shared_service_container_blocks(self):
        jobs = self._jobs("""\
jobs:
  a:
    services:
      pg:
        image: postgres
    steps: []
  b:
    needs: [a]
    steps: []
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_deployment_environment_blocks(self):
        jobs = self._jobs("""\
jobs:
  a:
    environment: production
    steps: []
  b:
    needs: [a]
    steps: []
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_needs_reference_in_an_if_condition_blocks(self):
        jobs = self._jobs("""\
jobs:
  a:
    steps: []
  b:
    needs: [a]
    if: ${{ needs.a.result == 'success' }}
    steps: []
""")
        assert independence_verdict(jobs["b"], jobs["a"], "a").independent is False

    def test_upload_without_download_is_still_independent(self):
        jobs = self._jobs("""\
jobs:
  a:
    steps:
      - uses: actions/upload-artifact@v4
  b:
    needs: [a]
    steps:
      - run: make
""")
        v = independence_verdict(jobs["b"], jobs["a"], "a")
        assert v.independent is True

    def test_cheap_gate_lowers_confidence(self):
        """Removing a fail-fast guard trades safety for speed -- that is a policy call,
        so it must not be presented with the same confidence as a pure win."""
        jobs = self._jobs("""\
jobs:
  lint:
    steps:
      - run: ruff check .
  build:
    needs: [lint]
    steps:
      - run: make
""")
        v = independence_verdict(jobs["build"], jobs["lint"], "lint")
        assert v.independent is True
        assert v.confidence < 0.75


# ───────────────────────────────────────────────────────── cache

NO_CACHE = """\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
"""

RUN_ID_KEY = """\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache@v4
        with:
          path: ~/.npm
          key: npm-${{ github.run_id }}
      - run: npm ci
"""

WITH_SETUP_CACHE = """\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-node@v4
        with:
          cache: npm
      - run: npm ci
"""


class TestCacheDetector:
    def test_flags_a_key_that_can_never_hit(self):
        drafts = DependencyCacheDetector().run(make_ctx(RUN_ID_KEY))
        kinds = [d.kind for d in drafts]
        assert "cache_key_never_hits" in kinds
        never = next(d for d in drafts if d.kind == "cache_key_never_hits")
        assert never.confidence > 0.9  # config alone is conclusive

    def test_missing_cache_flagged_when_install_duration_is_flat(self):
        series = {("build", "npm ci"): StepSeries("build", "npm ci", [90.0] * 20, list(range(20)))}
        ctx = make_ctx(NO_CACHE, step_series=series)
        drafts = DependencyCacheDetector().run(ctx)
        found = [d for d in drafts if d.kind == "no_dependency_cache"]
        assert len(found) == 1
        assert found[0].savings.is_range  # projection, never a point estimate

    def test_bimodal_duration_suppresses_the_finding(self):
        """Wide spread means something is already being restored -- flagging it would be
        a false positive."""
        series = {("build", "npm ci"): StepSeries(
            "build", "npm ci", [90.0, 5.0] * 10, list(range(20)))}
        ctx = make_ctx(NO_CACHE, step_series=series)
        drafts = [d for d in DependencyCacheDetector().run(ctx)
                  if d.kind == "no_dependency_cache"]
        assert drafts == []

    def test_setup_action_cache_counts_as_cached(self):
        series = {("build", "npm ci"): StepSeries("build", "npm ci", [90.0] * 20, list(range(20)))}
        ctx = make_ctx(WITH_SETUP_CACHE, step_series=series)
        drafts = [d for d in DependencyCacheDetector().run(ctx)
                  if d.kind == "no_dependency_cache"]
        assert drafts == []


# ───────────────────────────────────────────────────────── drafts + cost

class TestFindingDraftInvariants:
    def test_a_finding_without_evidence_is_rejected_at_construction(self):
        """The DB enforces this at commit; the dataclass enforces it before a detector
        can even return one."""
        with pytest.raises(ValueError, match="no evidence"):
            FindingDraft(
                kind="x", module="waste", severity=3, confidence=0.9, dedupe_key="k",
                title="t", detector_version="v", evidence=[],
            )

    def test_out_of_range_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            FindingDraft(
                kind="x", module="waste", severity=3, confidence=1.5, dedupe_key="k",
                title="t", detector_version="v",
                evidence=[EvidenceDraft(kind="run_history", run_ids=[1])],
            )


class TestCostCurrency:
    def test_public_repo_on_standard_runners_leads_with_hours(self):
        ctx = CostContext(is_private=False, runs_per_month=100, rate_card=RATE_CARD)
        assert ctx.headline_currency is Currency.HOURS
        assert ctx.dollars_per_month(60) == 0.0

    def test_public_repo_on_larger_runners_is_billed(self):
        """Larger runners are billed even on public repos, so the test is the effective
        rate rather than the visibility flag."""
        ctx = CostContext(is_private=False, runs_per_month=100, rate_card=RATE_CARD,
                          dominant_labels=["ubuntu-latest-8-core"])
        assert ctx.headline_currency is Currency.DOLLARS
        assert ctx.dollars_per_month(60) > 0

    def test_private_repo_leads_with_dollars(self):
        ctx = CostContext(is_private=True, runs_per_month=100, rate_card=RATE_CARD)
        assert ctx.headline_currency is Currency.DOLLARS
        assert ctx.dollars_per_month(60) == pytest.approx(0.6)  # 1 min * 100 runs * $0.006

    def test_hypothetical_dollars_offered_for_public_repos(self):
        ctx = CostContext(is_private=False, runs_per_month=100, rate_card=RATE_CARD)
        assert ctx.hypothetical_dollars_per_month(60) > 0

    def test_unknown_runner_label_bills_nothing_rather_than_guessing(self):
        ctx = CostContext(is_private=True, runs_per_month=100, rate_card=RATE_CARD,
                          dominant_labels=["self-hosted-gpu"])
        assert ctx.dollars_per_month(60) == 0.0


class TestCancellationIgnoresQueueTime:
    """Queue time is not consumed compute.

    Regression for sveltejs/kit run 33123664062: created 2026-08-27, started 2026-08-31,
    executed for 79 seconds. Measured from queue entry it appeared to span four days and
    overlap every other run in the window, contributing 323,357s of "waste" -- 98% of that
    repo's reported total -- and inflating its recoverable share to 5,132%.
    """

    @staticmethod
    def _findings(runs):
        ctx = make_ctx(NO_CONCURRENCY, runs=runs)
        return NoRunCancellationDetector().run(ctx)

    def test_a_long_queue_wait_is_not_counted_as_waste(self):
        # Two runs on one branch. The first waited four days to start, then ran 100s.
        # Overlap must be measured from execution, so these barely overlap at all.
        four_days = 4 * 24 * 3600
        runs = [
            run_obs(1, "main", 0.0, four_days + 100.0, queue_seconds=four_days),
            run_obs(2, "main", four_days + 50.0, four_days + 150.0, queue_seconds=0.0),
        ]
        found = self._findings(runs)
        if found:
            # Whatever is reported cannot exceed the time the run actually executed.
            assert found[0].savings.seconds_per_run <= 100.0

    def test_genuinely_overlapping_execution_is_still_caught(self):
        """The fix must not silence the rule it is correcting."""
        runs = [
            run_obs(1, "main", 0.0, 600.0),
            run_obs(2, "main", 100.0, 700.0),
        ]
        found = self._findings(runs)
        assert found, "an ordinary superseded run must still be reported"
        assert found[0].savings.seconds_per_run > 0

    def test_waste_never_exceeds_the_run_that_produced_it(self):
        """The invariant the old code broke: you cannot waste more than you ran."""
        runs = [
            run_obs(i, "main", float(i * 10), float(i * 10 + 300), queue_seconds=3600.0)
            for i in range(5)
        ]
        found = self._findings(runs)
        if found:
            assert found[0].savings.seconds_per_run <= 300.0
