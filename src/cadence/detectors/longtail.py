"""Class D — the long tail: a few steps consuming most of the pipeline's time.

The design doc's framing is "top 5% of steps consume >50% of suite wall time". This rule
does not propose a fix, because the right fix depends on what the step *is* — split it,
parallelise it, cache it, or move it to nightly. It surfaces the concentration and names
the step, which is the part a maintainer cannot easily see for themselves.

It carries no savings figure at all. That is deliberate: any number here would be a
guess about a refactor we have not specified, and the product's rule is that a claim
without evidence does not ship. The finding's value is the ranking, not a dollar amount.
"""

from __future__ import annotations

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext

DETECTOR_ID = "waste.long_tail_step"
DETECTOR_VERSION = "long_tail_step@1"

MIN_OBSERVATIONS = 20
# Only worth raising when concentration is genuinely lopsided.
MIN_SHARE_OF_TOTAL = 0.30
MIN_MEDIAN_SECONDS = 60.0


class LongTailStepDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        series = [s for s in ctx.step_series.values() if len(s.durations) >= MIN_OBSERVATIONS]
        if not series:
            return []

        # Total observed step time, as the denominator for concentration.
        total = sum(sum(s.durations) for s in series)
        if total <= 0:
            return []

        ranked = sorted(series, key=lambda s: sum(s.durations), reverse=True)
        out: list[FindingDraft] = []

        for s in ranked[:3]:
            share = sum(s.durations) / total
            ordered = sorted(s.durations)
            median = ordered[len(ordered) // 2]
            if share < MIN_SHARE_OF_TOTAL or median < MIN_MEDIAN_SECONDS:
                continue

            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            spread = (p95 / median) if median > 0 else 1.0

            out.append(
                FindingDraft(
                    kind="long_tail_step",
                    module="waste",
                    severity=2,
                    confidence=0.90,  # a measurement, not an inference
                    dedupe_key=f"long_tail_step:{s.job_key}:{s.step_name}",
                    title=(
                        f"`{s.step_name}` in `{s.job_key}` is {share:.0%} of all "
                        f"observed step time (p50 {_mmss(median)}, p95 {_mmss(p95)})"
                    ),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=(
                        "This step dominates the pipeline. Options, in rough order of "
                        "effort: shard it across jobs, cache its inputs, or move it to a "
                        "nightly run."
                        + (
                            f" Its p95 is {spread:.1f}x its median, so it is also "
                            f"inconsistent — worth checking why before splitting it."
                            if spread >= 2.0
                            else ""
                        )
                    ),
                    # No savings figure: the fix is unspecified, so any number would be
                    # a guess dressed as a measurement.
                    savings=None,
                    evidence=[
                        EvidenceDraft(
                            kind="timing_series",
                            payload={
                                "step": s.step_name,
                                "job": s.job_key,
                                "observations": len(s.durations),
                                "share_of_total_step_time": round(share, 4),
                                "p50_seconds": round(median, 1),
                                "p95_seconds": round(p95, 1),
                                "durations": [round(d, 1) for d in s.durations[:100]],
                            },
                        ),
                        EvidenceDraft(
                            kind="run_history",
                            run_ids=s.run_ids[:50],
                            payload={"note": "runs this step was observed in"},
                        ),
                    ],
                )
            )
        return out


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"
