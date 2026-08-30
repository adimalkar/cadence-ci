"""Per-job billing rounding.

The two suppression rules matter more than the arithmetic. This detector must stay silent
on a repo that is not billed, and it must never claim wall-clock time it does not recover.
Both are easy to get wrong in a way that reads as a bigger, better finding.
"""

from __future__ import annotations

import math

import pytest

from cadence.cost import CostContext, RateCard
from cadence.detectors.billing import (
    MIN_JOBS,
    JobBillingRoundingDetector,
    profile_rounding,
)
from cadence.detectors.context import AuditContext

HOSTED = "ubuntu-latest"
BIG = "ubuntu-latest-8-core"

# ubuntu-latest is free on public repos; the larger runner is billed even there.
CARD = RateCard(
    version=20260301,
    rates={HOSTED: 0.006, BIG: 0.024, "__self_hosted__": 0.002},
    free_on_public={HOSTED: True, BIG: False, "__self_hosted__": True},
)


def ctx(
    leg_durations: dict[tuple[str, str], list[float]],
    *,
    is_private: bool = True,
    labels: list[str] | None = None,
    window_days: int = 30,
) -> AuditContext:
    cost = CostContext(
        is_private=is_private,
        dominant_labels=labels or [HOSTED],
        runs_per_month=100.0,
        rate_card=CARD,
    )
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=is_private,
        workflows=[], runs=[], step_series={}, cost=cost, window_days=window_days,
        leg_durations=leg_durations,
    )


def short_jobs(n: int, seconds: float = 20.0) -> dict[tuple[str, str], list[float]]:
    return {("ci.yml", "quick"): [seconds] * n}


class TestArithmetic:
    def test_billed_time_rounds_each_job_up_to_a_minute(self):
        p = profile_rounding(ctx(short_jobs(10, 20.0)))
        assert p.jobs == 10
        assert p.actual_seconds == pytest.approx(200.0)
        assert p.billed_seconds == pytest.approx(600.0)   # 10 jobs × 1 minute
        assert p.wasted_seconds == pytest.approx(400.0)
        assert p.wasted_fraction == pytest.approx(2 / 3)

    def test_a_job_already_over_a_minute_wastes_only_its_remainder(self):
        p = profile_rounding(ctx({("ci.yml", "build"): [61.0]}))
        assert p.billed_seconds == pytest.approx(120.0)
        assert p.wasted_seconds == pytest.approx(59.0)

    def test_exact_minutes_waste_nothing(self):
        p = profile_rounding(ctx({("ci.yml", "build"): [60.0, 120.0]}))
        assert p.wasted_seconds == pytest.approx(0.0)
        assert p.wasted_fraction == 0.0

    def test_zero_and_negative_durations_are_ignored_not_counted(self):
        p = profile_rounding(ctx({("ci.yml", "j"): [0.0, -5.0, 30.0]}))
        assert p.jobs == 1
        assert p.actual_seconds == pytest.approx(30.0)

    def test_empty_input_is_safe(self):
        p = profile_rounding(ctx({}))
        assert p.jobs == 0 and p.wasted_fraction == 0.0

    def test_offenders_are_ranked_by_waste_and_exclude_long_jobs(self):
        p = profile_rounding(ctx({
            ("ci.yml", "tiny"): [5.0] * 20,      # 20 × 55s wasted
            ("ci.yml", "small"): [30.0] * 5,     # 5 × 30s wasted
            ("ci.yml", "long"): [300.0] * 10,    # over a minute — not a candidate
        }))
        names = [o[1] for o in p.offenders]
        assert names == ["tiny", "small"]
        assert "long" not in names


class TestSuppression:
    """Where the rule must stay quiet."""

    def test_silent_on_a_public_repo_with_free_runners(self):
        """The corpus case. The rounding is real; nobody is charged for it."""
        found = JobBillingRoundingDetector().run(
            ctx(short_jobs(200), is_private=False, labels=[HOSTED])
        )
        assert found == []

    def test_fires_on_a_public_repo_using_a_billed_larger_runner(self):
        """Larger runners are billed even on public repos, so the money is real."""
        found = JobBillingRoundingDetector().run(
            ctx(short_jobs(200), is_private=False, labels=[BIG])
        )
        assert len(found) == 1

    def test_fires_on_a_private_repo(self):
        # 600 jobs, not 200: at $0.006/min, 200 short jobs waste $0.80/month and are
        # correctly suppressed by the money floor. Volume has to be realistic for the
        # money to be.
        found = JobBillingRoundingDetector().run(
            ctx(short_jobs(600), is_private=True, labels=[HOSTED])
        )
        assert len(found) == 1

    def test_a_small_private_repo_is_below_the_money_floor(self):
        """The suppression the test above tripped over, asserted deliberately."""
        found = JobBillingRoundingDetector().run(
            ctx(short_jobs(200), is_private=True, labels=[HOSTED])
        )
        assert found == []

    def test_silent_below_the_job_threshold(self):
        found = JobBillingRoundingDetector().run(ctx(short_jobs(MIN_JOBS - 1)))
        assert found == []

    def test_silent_when_jobs_are_already_minute_sized(self):
        """A tight pipeline gets told nothing, which is the correct empty state."""
        found = JobBillingRoundingDetector().run(
            ctx({("ci.yml", "build"): [58.0] * 100})
        )
        # 2s wasted per 60s billed is ~3%, well under the floor.
        assert found == []

    def test_silent_when_the_money_rounds_to_nothing(self):
        """A high percentage of a tiny bill is still a tiny bill."""
        found = JobBillingRoundingDetector().run(
            ctx({("ci.yml", "j"): [1.0] * MIN_JOBS}, labels=[HOSTED])
        )
        assert found == []


class TestTheFinding:
    @staticmethod
    def only(**kw):
        found = JobBillingRoundingDetector().run(ctx(short_jobs(600), **kw))
        assert len(found) == 1
        return found[0]

    def test_claims_no_wall_clock_saving(self):
        """The property this whole design exists to protect.

        Savings feeds the replay total, which renders as wall-clock hours recovered in the
        headline. Merging short jobs recovers money, not elapsed time -- so a Savings here
        would put billed seconds into a wall-clock number.
        """
        f = self.only()
        assert f.savings is None

    def test_says_so_explicitly_in_evidence_too(self):
        f = self.only()
        payload = f.evidence[0].payload or {}
        assert payload["wall_clock_seconds_recovered"] == 0

    def test_evidence_carries_the_full_arithmetic(self):
        f = self.only()
        p = f.evidence[0].payload or {}
        assert p["jobs_observed"] == 600
        assert p["billed_minutes"] == pytest.approx(600.0)
        assert p["actual_minutes"] == pytest.approx(200.0)
        assert p["wasted_minutes"] == pytest.approx(400.0)
        assert p["usd_per_minute"] == 0.006

    def test_dollars_are_scaled_to_a_month_from_the_window(self):
        f = self.only(window_days=60)
        # 400 wasted minutes over 60 days = 200/month at $0.006
        assert (f.evidence[0].payload or {})["usd_per_month"] == pytest.approx(1.2)

    def test_warns_that_merging_trades_wall_clock_for_money(self):
        """An honest finding names its own downside."""
        f = self.only()
        assert "critical path" in (f.suggested_action or "")
        assert "parallelism" in (f.suggested_action or "")

    def test_names_the_offending_jobs(self):
        f = self.only()
        offenders = (f.evidence[1].payload or {})["worst_offenders"]
        assert offenders[0]["job"] == "quick"
        assert offenders[0]["occurrences"] == 600

    def test_one_finding_per_repo_not_per_workflow(self):
        """Splitting per file would double-count one pool of minutes."""
        found = JobBillingRoundingDetector().run(ctx({
            ("a.yml", "j"): [20.0] * 100,
            ("b.yml", "j"): [20.0] * 100,
            ("c.yml", "j"): [20.0] * 100,
        }))
        assert len(found) == 1

    def test_severity_rises_with_the_bill(self):
        cheap = self.only()
        expensive = JobBillingRoundingDetector().run(
            ctx({("ci.yml", "j"): [20.0] * 400_000}, labels=[BIG])
        )[0]
        assert expensive.severity > cheap.severity


class TestAgainstTheCorpusMeasurement:
    def test_reproduces_the_flask_shape(self):
        """pallets/flask: ~1,142 jobs averaging 20s, measured at 67.3% rounding waste."""
        p = profile_rounding(ctx({("ci.yml", "test"): [20.0] * 1142}))
        assert p.wasted_fraction == pytest.approx(0.667, abs=0.01)

    def test_reproduces_the_react_shape(self):
        """react/react: ~17,881 jobs averaging 71s, measured at 30.2%."""
        # 71s bills as 120s, so 49/120 ≈ 40.8% for a uniform 71s; the real spread is
        # wider. What is asserted here is the direction and rough scale, not the exact
        # corpus figure -- a uniform stand-in cannot reproduce a distribution.
        p = profile_rounding(ctx({("ci.yml", "test"): [71.0] * 17881}))
        assert 0.25 < p.wasted_fraction < 0.45

    def test_billing_matches_a_naive_independent_implementation(self):
        durations = [3.0, 17.0, 59.0, 60.0, 61.0, 119.0, 240.5]
        p = profile_rounding(ctx({("ci.yml", "j"): durations}))
        expected = sum(math.ceil(d / 60.0) * 60.0 for d in durations)
        assert p.billed_seconds == pytest.approx(expected)
