"""The first-failing-step index.

The arithmetic is a `Counter`, so the tests that matter are the ones about restraint: the
detector must stay silent on thin data and on a flat distribution, and it must never let
its infrastructure allowlist imply a split it did not measure.

That last one is the reason this detector exists in the shape it does. `FEATURE_CANDIDATES`
F8 promised "your tests" versus "infrastructure, not you"; measurement put recognisable
infrastructure at 2-5% of first failures, so the split is reported as a secondary number
with its own coverage attached, never as the headline.
"""

from __future__ import annotations

from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, JobFailure
from cadence.detectors.failure import (
    MIN_FAILURES,
    MIN_TOP_SHARE,
    FirstFailingStepDetector,
    classify_step,
)

CARD = RateCard(
    version=20260301,
    rates={"ubuntu-latest": 0.006},
    free_on_public={"ubuntu-latest": True},
)


def ctx(failures: list[JobFailure], *, window_days: int = 30) -> AuditContext:
    cost = CostContext(
        is_private=False,
        dominant_labels=["ubuntu-latest"],
        runs_per_month=100.0,
        rate_card=CARD,
    )
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=[], runs=[], step_series={}, cost=cost, window_days=window_days,
        failures=failures,
    )


def fails(step: str, n: int, *, start_run: int = 1, seconds: float = 120.0) -> list[JobFailure]:
    return [
        JobFailure(
            run_id=start_run + i, job_name="test", step_name=step,
            step_number=3, job_seconds=seconds,
        )
        for i in range(n)
    ]


def run(failures: list[JobFailure], **kw):
    return FirstFailingStepDetector().run(ctx(failures, **kw))


# --- silence ---------------------------------------------------------------------------


def test_silent_below_the_failure_floor():
    """A handful of failures is not a distribution."""
    assert run(fails("pytest", MIN_FAILURES - 1)) == []


def test_silent_when_no_failures_at_all():
    """A repo whose runs all passed is a legitimate state, not a coverage gap."""
    assert run([]) == []


def test_silent_when_the_distribution_is_flat():
    """No single place to look means nothing worth saying.

    Ten steps failing four times each is a true observation and a useless finding.
    """
    spread = []
    for i in range(10):
        spread += fails(f"step-{i}", 4, start_run=100 * i)
    assert len(spread) >= MIN_FAILURES
    assert run(spread) == []


def test_fires_exactly_at_the_share_boundary():
    """The guard is a floor, not a preference: at the threshold it fires."""
    total = 100
    top = int(MIN_TOP_SHARE * total)
    failures = fails("pytest", top) + [
        JobFailure(run_id=1000 + i, job_name="t", step_name=f"other-{i}",
                   step_number=2, job_seconds=10.0)
        for i in range(total - top)
    ]
    out = run(failures)
    assert len(out) == 1
    assert out[0].evidence[1].payload["top_step_share"] == MIN_TOP_SHARE


# --- what it reports -------------------------------------------------------------------


def test_reports_concentration_with_counts_in_the_title():
    out = run(fails("pytest", 60) + fails("ruff", 40, start_run=500))
    assert len(out) == 1
    d = out[0]
    assert "60%" in d.title
    assert "pytest" in d.title
    assert "60 of 100" in d.title
    assert d.module == "flake"


def test_carries_no_savings():
    """The minutes were really spent; no config change recovers them.

    A savings figure here would be a guess about work we have not specified, which is the
    same trade `long_tail_step` makes.
    """
    (d,) = run(fails("pytest", 40))
    assert d.savings is None


def test_evidence_is_attached_and_cites_runs():
    (d,) = run(fails("pytest", 40))
    assert len(d.evidence) == 2
    history = d.evidence[0]
    assert history.kind == "run_history"
    assert len(history.run_ids) > 0
    assert len(history.run_ids) <= 50


def test_run_ids_are_only_those_at_the_top_step():
    """Evidence must cite the failures it is about, not every failure in the window."""
    failures = fails("pytest", 30, start_run=1) + fails("ruff", 10, start_run=900)
    (d,) = run(failures)
    cited = set(d.evidence[0].run_ids)
    assert cited <= {f.run_id for f in failures if f.step_name == "pytest"}
    assert not cited & {f.run_id for f in failures if f.step_name == "ruff"}


def test_distribution_is_capped_and_ordered():
    # One dominant step so the share guard passes, then a long tail to be truncated.
    failures: list[JobFailure] = fails("pytest", 60)
    for i in range(15):
        failures += fails(f"step-{i}", 15 - i, start_run=1000 * (i + 1))
    (d,) = run(failures)
    dist = d.evidence[1].payload["distribution"]
    assert len(dist) == 10
    assert [row["failures"] for row in dist] == sorted(
        (row["failures"] for row in dist), reverse=True
    )


# --- the classifier, and its honesty ----------------------------------------------------


def test_classify_recognises_infrastructure_steps():
    assert classify_step("Run actions/checkout@v4") == "checkout"
    assert classify_step("Set up job") == "runner_setup"
    assert classify_step("Run actions/setup-python@v5") == "toolchain"
    assert classify_step("npm ci") == "dependencies"
    # A known action's post phase classifies as that action, not as a generic post step:
    # "the checkout cleanup failed" is more useful than "a post step failed".
    assert classify_step("Post Run actions/checkout@v4") == "checkout"
    # `post_step` is the fallback for the post phase of an action we do not recognise.
    assert classify_step("Post Run acme/deploy-thing@v2") == "post_step"


def test_classify_refuses_to_guess():
    """None is the honest answer for a project's own command."""
    assert classify_step("Run all tests on GPU") is None
    assert classify_step("make test") is None
    assert classify_step("Test without coverage") is None


def test_coverage_is_published_even_when_it_is_low():
    """The number that stops an unclassified majority reading as 'not infrastructure'.

    This is the guard against F8's original overclaim, so it is asserted rather than
    trusted.
    """
    (d,) = run(fails("Run all tests on GPU", 40))
    payload = d.evidence[1].payload
    assert payload["classification_coverage"] == 0.0
    assert payload["infrastructure_failures"] == 0
    assert payload["top_step_infrastructure"] is None


def test_infrastructure_top_step_changes_the_advice():
    (infra,) = run(fails("Run actions/checkout@v4", 40))
    (own,) = run(fails("make test", 40))
    assert "checkout" in infra.evidence[1].payload["top_step_infrastructure"]
    assert "environmental" in infra.suggested_action
    assert "your own build or test command" in own.suggested_action


def test_coverage_counts_every_infrastructure_failure_not_just_the_top():
    """Coverage is over the whole distribution, or it is not coverage."""
    failures = fails("make test", 30) + fails("npm ci", 20, start_run=800)
    (d,) = run(failures)
    payload = d.evidence[1].payload
    assert payload["infrastructure_failures"] == 20
    assert payload["classification_coverage"] == 0.4
    # The headline is still the concentration, not the split.
    assert payload["top_step"] == "make test"


# --- presentation -----------------------------------------------------------------------


def test_long_step_names_are_trimmed_in_the_title():
    long_name = "Run uv run --locked --no-default-groups --group dev tox run -e py312-full"
    (d,) = run(fails(long_name, 40))
    assert "…" in d.title
    # The untrimmed name still reaches the evidence, where it is not a display problem.
    assert d.evidence[1].payload["top_step"] == long_name


def test_dedupe_key_is_semantic_not_positional():
    """Keyed on the step, so re-ordering the workflow does not orphan a suppression."""
    (d,) = run(fails("pytest", 40))
    assert d.dedupe_key == "first_failing_step:pytest"
