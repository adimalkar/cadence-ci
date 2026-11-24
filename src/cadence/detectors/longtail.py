"""Class D — the long tail: a few steps consuming most of the pipeline's time.

The design doc's framing is "top 5% of steps consume >50% of suite wall time". This rule
does not propose a fix, because the right fix depends on what the step *is* — split it,
parallelise it, cache it, or move it to nightly. It surfaces the concentration and names
the step, which is the part a maintainer cannot easily see for themselves.

It carries no savings figure at all. That is deliberate: any number here would be a
guess about a refactor we have not specified, and the product's rule is that a claim
without evidence does not ship. The finding's value is the ranking, not a dollar amount.

Timing statistics are computed within one triggering event (CAVEATS 83). A workflow can
do very different work on a push to main than on a pull request -- a guard step that takes
half a minute on a PR and half an hour on main -- and a median over both describes neither.
Concentration is still measured as before, against all observed step time, because "this
step is X% of the time CI spends" stays true across events; only p50, p95 and the median
floor are taken per event, and the finding names the events where the step is slow.
Dividing by each event's own step time instead was tried and rejected: an event that runs
one small workflow makes any of its steps look concentrated (it doubled corpus findings).
"""

from __future__ import annotations

from dataclasses import dataclass

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, StepSeries

DETECTOR_ID = "waste.long_tail_step"
DETECTOR_VERSION = "long_tail_step@3"

MIN_OBSERVATIONS = 20
# Only worth raising when concentration is genuinely lopsided.
MIN_SHARE_OF_TOTAL = 0.30
MIN_MEDIAN_SECONDS = 60.0
# How many of the heaviest steps are considered.
TOP_STEPS = 3
# A segment of the same step this much faster than the flagged one is named as a contrast:
# it says the slow path depends on the event, which is the first thing to look at.
CONTRAST_RATIO = 0.5

StreamKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Segment:
    """One step's history within one triggering event."""

    event: str | None
    series: StepSeries
    p50: float
    p95: float
    share: float  # of all measured step time, like the stream's own share

    @property
    def total(self) -> float:
        return sum(self.series.durations)


def split_by_event(ctx: AuditContext) -> dict[StreamKey, dict[str | None, StepSeries]]:
    """Each resolved step history, partitioned by the event of the run it came from.

    A run the context does not describe, or a series whose run ids do not line up with its
    durations, contributes to the unknown (None) event rather than being guessed at.
    """
    events = {r.run_id: r.event for r in ctx.runs}
    out: dict[StreamKey, dict[str | None, StepSeries]] = {}
    for key, s in ctx.step_series_resolved.items():
        parts: dict[str | None, StepSeries] = {}
        aligned = len(s.run_ids) == len(s.durations)
        for i, duration in enumerate(s.durations):
            run_id = s.run_ids[i] if aligned else None
            event = events.get(run_id) if run_id is not None else None
            part = parts.get(event)
            if part is None:
                part = parts[event] = StepSeries(job_key=s.job_key, step_name=s.step_name)
            part.durations.append(duration)
            if run_id is not None:
                part.run_ids.append(run_id)
        out[key] = parts
    return out


def _segment(event: str | None, series: StepSeries, **extra) -> Segment:
    ordered = sorted(series.durations)
    return Segment(
        event=event, series=series,
        p50=ordered[len(ordered) // 2],
        p95=ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        **extra,
    )


class LongTailStepDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        # A runtime display name can belong to several workflows. Only rank histories
        # resolved within their own workflow, and apply the observation floor per stream.
        series = [(key, s) for key, s in ctx.step_series_resolved.items()
                  if len(s.durations) >= MIN_OBSERVATIONS]
        if not series:
            return []

        # Total observed step time, as the denominator for concentration.
        total = sum(sum(s.durations) for _, s in series)
        if total <= 0:
            return []

        split = split_by_event(ctx)
        # Tie-break by identity so observation order cannot change which steps appear.
        ranked = sorted(series, key=lambda item: (-sum(item[1].durations), item[0]))
        out: list[FindingDraft] = []
        for key, s in ranked[:TOP_STEPS]:
            share = sum(s.durations) / total
            if share < MIN_SHARE_OF_TOTAL:
                continue
            # The median floor applies per event, and so does the observation floor: two
            # thin event histories must not pool into one median that describes neither.
            segments = [_segment(event, part, share=sum(part.durations) / total)
                        for event, part in split[key].items()]
            flagged = [seg for seg in segments
                       if len(seg.series.durations) >= MIN_OBSERVATIONS
                       and seg.p50 >= MIN_MEDIAN_SECONDS]
            if flagged:
                out.append(self._finding(key, share, total, len(series), flagged, segments))
        return out

    def _finding(self, key: StreamKey, share: float, total: float, eligible: int,
                 flagged: list[Segment], segments: list[Segment]) -> FindingDraft:
        workflow_path, job_key, step_name = key
        flagged = sorted(flagged, key=lambda seg: (-seg.total, seg.event or ""))
        primary = flagged[0]
        flagged_events = {seg.event for seg in flagged}
        # Same step, other events, enough history to say it is clearly faster there.
        contrasts = sorted(
            (seg for seg in segments
             if seg.event not in flagged_events
             and len(seg.series.durations) >= MIN_OBSERVATIONS
             and seg.p50 <= primary.p50 * CONTRAST_RATIO),
            key=lambda seg: (seg.p50, seg.event or ""),
        )

        stats = f"p50 {_mmss(primary.p50)}, p95 {_mmss(primary.p95)}"
        details = [f"`{primary.event}` runs: {stats}" if primary.event else stats]
        details += [f"`{seg.event or 'unknown event'}` p50 {_mmss(seg.p50)}"
                    for seg in flagged[1:]]
        details += [f"but `{c.event or 'unknown event'}` p50 {_mmss(c.p50)}"
                    for c in contrasts]
        title = (f"`{step_name}` in `{job_key}` ({workflow_path}) is {share:.0%} of "
                 f"measured step time ({'; '.join(details)})")

        spread = (primary.p95 / primary.p50) if primary.p50 > 0 else 1.0
        action = (
            "This step dominates observed step time. Options, in rough order of "
            "effort: shard it across jobs, cache its inputs, or move it to a "
            "nightly run."
        )
        if contrasts:
            fast = contrasts[0]
            action += (
                f" It takes {_mmss(fast.p50)} at the median in "
                f"`{fast.event or 'unknown event'}` runs, so the slow path depends on the "
                f"trigger: check what it does differently on "
                f"`{primary.event or 'unknown event'}` before splitting it."
            )
        if spread >= 2.0:
            action += (f" Its p95 is {spread:.1f}x its median, so it is also "
                       f"inconsistent — worth checking why before splitting it.")

        # The primary event's runs first, so the 50-run cap never drops the runs the
        # headline statistics came from.
        cited = list(dict.fromkeys(rid for seg in flagged for rid in seg.series.run_ids))
        return FindingDraft(
            kind="long_tail_step",
            module="waste",
            severity=2,
            confidence=0.90,  # a measurement, not an inference
            # Per step, as in @2: segmenting by event changes the statistics, not which
            # step the finding is about, so existing suppressions keep matching.
            dedupe_key=f"long_tail_step:{workflow_path}:{job_key}:{step_name}",
            title=title,
            detector_version=DETECTOR_VERSION,
            suggested_action=action,
            # No savings figure: the fix is unspecified, so any number would be
            # a guess dressed as a measurement.
            savings=None,
            evidence=[
                EvidenceDraft(
                    kind="timing_series",
                    file_path=workflow_path,
                    payload={
                        "workflow": workflow_path,
                        "step": step_name,
                        "job": job_key,
                        "eligible_series": eligible,
                        "minimum_observations": MIN_OBSERVATIONS,
                        "total_step_seconds": round(total, 1),
                        "share_of_total_step_time": round(share, 4),
                        # The headline statistics, from the slow event's runs only.
                        "event": primary.event,
                        "observations": len(primary.series.durations),
                        "p50_seconds": round(primary.p50, 1),
                        "p95_seconds": round(primary.p95, 1),
                        "durations": [round(d, 1) for d in primary.series.durations[:100]],
                        # Every event this step was observed under, flagged or not, so a
                        # reader can see the split rather than trust it.
                        "by_event": [
                            {
                                "event": seg.event,
                                "flagged": seg.event in flagged_events,
                                "observations": len(seg.series.durations),
                                "p50_seconds": round(seg.p50, 1),
                                "p95_seconds": round(seg.p95, 1),
                                "share_of_total_step_time": round(seg.share, 4),
                            }
                            for seg in sorted(segments,
                                              key=lambda seg: (-seg.total, seg.event or ""))
                        ],
                    },
                ),
                EvidenceDraft(
                    kind="run_history",
                    file_path=workflow_path,
                    run_ids=cited[:50],
                    payload={"note": "runs this step was observed in, in flagged events"},
                ),
            ],
        )


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"
