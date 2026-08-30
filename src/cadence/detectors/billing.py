"""Per-job billing rounding.

GitHub bills each job **rounded up to the whole minute**. A 20-second job costs a minute; a
matrix of forty 30-second jobs costs forty minutes for twenty minutes of work. Nobody
reasons about this, because the receipt is one aggregate number and the loss is spread
across thousands of tiny jobs.

Measured across the corpus on 2026-08-30: 1,002 hours of the 14,342 billed -- 7.0% -- were
rounding, and it concentrates brutally (`pallets/flask` 67.3% on 20-second jobs).

Two properties make this rule different from every other one in the catalog, and both are
enforced below rather than left to the report.

**It costs money, not time.** Merging two 20-second jobs into one 40-second job saves a
billed minute and saves the developer nothing -- the wall clock is unchanged or worse,
because two jobs ran in parallel and one does not. So this detector emits `savings=None`
and states the money in its title. Putting billed seconds into `Savings` would feed them
into the replay total, which renders as *wall-clock hours recovered* in the headline. That
number would be wrong.

**It is free on public repositories.** Standard GitHub-hosted runners cost nothing on a
public repo, so the rounding is real but unbilled. The detector asks the rate card what
this repo actually pays and stays silent when the answer is zero, rather than quoting a
figure nobody is charged. Larger runners are billed even on public repos, which is exactly
the case it still fires on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext

DETECTOR_ID = "waste.job_billing_rounding"
DETECTOR_VERSION = "job_billing_rounding@1"

# Below this, the finding is noise: a repo whose jobs are already minute-sized has nothing
# to recover, and saying so on every audit trains people to ignore us.
MIN_WASTE_FRACTION = 0.15

# Rounding on a handful of jobs is not a pattern. Detectors that fire on thin evidence are
# how a catalog loses credibility.
MIN_JOBS = 30

# A job at or above this duration cannot be meaningfully merged with a neighbour without
# hurting feedback time, so it is not a candidate even if it wastes a few seconds.
SHORT_JOB_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RoundingProfile:
    """What the rounding costs, and which jobs cause it."""

    jobs: int
    actual_seconds: float
    billed_seconds: float
    # (workflow_path, job_name) -> (occurrences, mean seconds, wasted seconds)
    offenders: list[tuple[str, str, int, float, float]]

    @property
    def wasted_seconds(self) -> float:
        return self.billed_seconds - self.actual_seconds

    @property
    def wasted_fraction(self) -> float:
        return (self.wasted_seconds / self.billed_seconds) if self.billed_seconds else 0.0


def profile_rounding(ctx: AuditContext) -> RoundingProfile:
    """Billed vs actual seconds across every observed job, plus the worst offenders.

    Pure arithmetic over durations already ingested -- no projection, no model. The billed
    figure is what GitHub charged, which is why this rule's evidence is replay-grade even
    though it carries no `Savings`.
    """
    jobs = 0
    actual = 0.0
    billed = 0.0
    offenders: list[tuple[str, str, int, float, float]] = []

    for (wf_path, leg), durations in ctx.leg_durations.items():
        usable = [d for d in durations if d > 0]
        if not usable:
            continue

        leg_actual = sum(usable)
        leg_billed = sum(math.ceil(d / 60.0) * 60.0 for d in usable)

        jobs += len(usable)
        actual += leg_actual
        billed += leg_billed

        mean = leg_actual / len(usable)
        if mean < SHORT_JOB_SECONDS:
            offenders.append(
                (wf_path, leg, len(usable), mean, leg_billed - leg_actual)
            )

    offenders.sort(key=lambda o: o[4], reverse=True)
    return RoundingProfile(
        jobs=jobs, actual_seconds=actual, billed_seconds=billed, offenders=offenders
    )


class JobBillingRoundingDetector:
    """Reports billed minutes lost to per-job rounding up.

    Emits at most one finding per repo. The waste is a property of the repo's job-size
    distribution, not of any single workflow, and splitting it per file would invite
    double-counting the same pool of minutes.
    """

    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        profile = profile_rounding(ctx)

        if profile.jobs < MIN_JOBS or not profile.offenders:
            return []
        if profile.wasted_fraction < MIN_WASTE_FRACTION:
            return []

        # What this repo is actually charged. Public repos on standard runners pay nothing,
        # and quoting a dollar figure to someone who is not billed is the fastest way to
        # lose the pitch -- the same reasoning cost.py applies to the headline currency.
        rate = ctx.cost.rate_card.usd_per_minute(
            ctx.cost.dominant_labels, is_private=ctx.is_private
        )
        if rate <= 0:
            return []

        window_months = max(ctx.window_days / 30.0, 1e-9)
        wasted_minutes_per_month = (profile.wasted_seconds / 60.0) / window_months
        usd_per_month = wasted_minutes_per_month * rate

        # Not worth a finding row for pennies, however large the percentage looks.
        if usd_per_month < 1.0:
            return []

        top = profile.offenders[:5]
        detail = ", ".join(
            f"`{leg}` ({count}× at {mean:.0f}s)" for _wf, leg, count, mean, _w in top
        )

        title = (
            f"{profile.wasted_fraction:.0%} of billed minutes are per-job rounding "
            f"— ${usd_per_month:,.0f}/month for compute never used"
        )

        evidence = [
            EvidenceDraft(
                kind="timing_series",
                payload={
                    "jobs_observed": profile.jobs,
                    "actual_minutes": round(profile.actual_seconds / 60.0, 1),
                    "billed_minutes": round(profile.billed_seconds / 60.0, 1),
                    "wasted_minutes": round(profile.wasted_seconds / 60.0, 1),
                    "wasted_fraction": round(profile.wasted_fraction, 4),
                    "usd_per_minute": rate,
                    "usd_per_month": round(usd_per_month, 2),
                    "window_days": ctx.window_days,
                    # Stated explicitly so no consumer mistakes this for time recovered.
                    "wall_clock_seconds_recovered": 0,
                },
            ),
            EvidenceDraft(
                kind="timing_series",
                payload={
                    "worst_offenders": [
                        {
                            "workflow": wf,
                            "job": leg,
                            "occurrences": count,
                            "mean_seconds": round(mean, 1),
                            "wasted_minutes": round(wasted / 60.0, 1),
                        }
                        for wf, leg, count, mean, wasted in top
                    ]
                },
            ),
        ]

        action = (
            f"These jobs finish well inside their billed minute: {detail}. "
            "Merging short jobs that share a runner recovers the rounding. "
            "Check the critical path first — fewer jobs means less parallelism, so this "
            "trades wall-clock feedback time for money rather than saving both."
        )

        return [
            FindingDraft(
                kind="job_billing_rounding",
                module="waste",
                severity=_severity(usd_per_month),
                # The arithmetic is exact: these minutes were billed. The uncertainty is
                # not in the measurement but in whether merging is acceptable, and that
                # is a judgement the maintainer makes, not a probability we assert.
                confidence=0.95,
                dedupe_key=f"job_billing_rounding:{ctx.repo_id}",
                title=title,
                detector_version=DETECTOR_VERSION,
                evidence=evidence,
                suggested_action=action,
                # Deliberately None. See the module docstring: billed seconds are not
                # wall-clock seconds, and Savings feeds the wall-clock headline.
                savings=None,
            )
        ]


def _severity(usd_per_month: float) -> int:
    if usd_per_month >= 500:
        return 4
    if usd_per_month >= 100:
        return 3
    return 2
