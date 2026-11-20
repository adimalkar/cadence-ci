"""Runs that wait a long time before any job starts executing.

`summarize_pipeline` already reports `queue_bound`, but it reads the *median* run of the
dominant workflow. A queue problem is a tail problem: most runs start at once and a
minority wait for an hour or more behind other work. The median never sees them.

Field case (CAVEATS 82): on a private repository with a single self-hosted runner, the
median CI run waited 0 seconds, and the audit reported "not queue-bound". Pushes to
`main` waited a p90 of more than 100 minutes for the runner, and the longest waited
almost four hours. That was the delay the team cared about most, and the audit missed it.

**What is measured.** For each (workflow, triggering event) stream, the wait is the time
between the run's first job being queued and its first job starting. Streams are kept
apart because a PR run and a push to the default branch of the same workflow often compete
for runners differently.

**What is not claimed.** No saving. The wait depends on everything else that occupied the
runners at that moment, so removing it has no exact replay, and the remedies (more
capacity, moving schedules, doing less work per event) are not ours to price. The cause is
not asserted either. Self-hosted capacity, GitHub's concurrent-job limits and approval
gates all look the same from timestamps alone. Workflows with any `environment:` job are
skipped, because a deployment waiting for a reviewer is not waiting for a runner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RunObservation

DETECTOR_ID = "waste.runner_queue_wait"
DETECTOR_VERSION = "runner_queue_wait@1"

# A stream's tail is meaningless on a handful of runs.
MIN_RUNS = 20

# The p90 wait at or above which a stream is reported. Ten minutes is well past any wait
# GitHub's own scheduling explains, and short enough to catch a feedback loop doubling.
MIN_P90_SECONDS = 600.0

# Reported, not gated on: the share of runs that waited at least this long. A nearest-rank
# p90 at or above MIN_P90_SECONDS already means at least a tenth of runs waited that long,
# so a separate share threshold would add no protection.
LONG_WAIT_SECONDS = 300.0

# A "wait" this long is a re-run carrying old timestamps, or an abandoned run, not a queue.
# Excluded rather than clamped, and counted in the evidence, as no_run_cancellation does.
MAX_PLAUSIBLE_WAIT_SECONDS = 24 * 3600.0

# Run IDs cited in evidence, worst first.
MAX_CITED_RUNS = 20


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list. Every value returned was observed."""
    if not sorted_values:
        raise ValueError("percentile of an empty list")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass(frozen=True, slots=True)
class WaitProfile:
    workflow: str
    event: str
    waits: list[tuple[int, float]]  # (run_id, seconds), ascending by seconds
    excluded: int
    runner_kind: str | None

    @property
    def seconds(self) -> list[float]:
        return [w for _, w in self.waits]

    @property
    def p50(self) -> float:
        return percentile(self.seconds, 0.50)

    @property
    def p90(self) -> float:
        return percentile(self.seconds, 0.90)

    @property
    def max(self) -> float:
        return self.seconds[-1]

    @property
    def long_wait_share(self) -> float:
        return sum(1 for w in self.seconds if w >= LONG_WAIT_SECONDS) / len(self.waits)


def wait_seconds(run: RunObservation) -> float | None:
    """Queue entry to first execution, or None when either end is missing."""
    if run.started_epoch is None or run.exec_started_epoch is None:
        return None
    return run.exec_started_epoch - run.started_epoch


def _runner_kind(runs: list[RunObservation]) -> str | None:
    kinds = {r.runner_kind for r in runs if r.runner_kind}
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else "mixed"


def profile_waits(ctx: AuditContext) -> list[WaitProfile]:
    """One profile per (workflow, event) stream with enough plausible observations."""
    gated = {
        wf.path
        for wf in ctx.workflows
        if any("environment" in job.raw for job in wf.jobs.values())
    }
    known = {wf.path for wf in ctx.workflows if not wf.parse_error}

    streams: dict[tuple[str, str], list[RunObservation]] = {}
    for run in ctx.runs:
        if run.workflow_path in known and run.workflow_path not in gated and run.event:
            streams.setdefault((run.workflow_path, run.event), []).append(run)

    out: list[WaitProfile] = []
    for (path, event), runs in sorted(streams.items()):
        waits: list[tuple[int, float]] = []
        excluded = 0
        for run in runs:
            w = wait_seconds(run)
            if w is None:
                continue
            # Negative: a re-run's carried-forward job started before the new attempt's
            # jobs were created. Implausibly long: not a queue. Neither is measurable.
            if w < 0 or w > MAX_PLAUSIBLE_WAIT_SECONDS:
                excluded += 1
                continue
            waits.append((run.run_id, w))
        if len(waits) < MIN_RUNS:
            continue
        waits.sort(key=lambda rw: (rw[1], rw[0]))
        out.append(
            WaitProfile(
                workflow=path, event=event, waits=waits, excluded=excluded,
                runner_kind=_runner_kind(runs),
            )
        )
    return out


def _mins(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _suggested(kind: str | None) -> str:
    if kind == "self-hosted":
        return (
            "These runs wait for a self-hosted runner. Every workflow that targets the same "
            "labels, scheduled ones included, competes for that pool. Options: add runner "
            "capacity, move heavy scheduled work away from busy hours, or do less work on "
            "this event. Check which runs held the runner during the longest waits."
        )
    if kind == "hosted":
        return (
            "These runs wait for a GitHub-hosted runner. That usually means the account's "
            "concurrent-job limit was reached by other workflows, or a larger-runner pool "
            "was saturated. Check what else ran during the longest waits."
        )
    return (
        "These runs wait before any job starts. Check what else held the runners during "
        "the longest waits."
    )


class RunnerQueueWaitDetector:
    """One finding per triggering event, listing every workflow whose stream qualifies.

    Runners are shared, so a queue is a property of the pool, not of one file. Reported
    per workflow, one saturated pool on a public corpus repo produced six findings with
    the same cause. Each stream still has to qualify on its own runs.
    """

    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        by_event: dict[str, list[WaitProfile]] = {}
        for prof in profile_waits(ctx):
            if prof.p90 >= MIN_P90_SECONDS:
                by_event.setdefault(prof.event, []).append(prof)

        drafts: list[FindingDraft] = []
        for event, profs in sorted(by_event.items()):
            pooled = sorted(
                (w for p in profs for w in p.waits), key=lambda rw: (rw[1], rw[0])
            )
            seconds = [w for _, w in pooled]
            p90 = percentile(seconds, 0.90)
            long_share = sum(1 for w in seconds if w >= LONG_WAIT_SECONDS) / len(seconds)
            kinds = {p.runner_kind for p in profs if p.runner_kind}
            kind = kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else None)
            names = ", ".join(p.workflow.rsplit("/", 1)[-1] for p in profs)
            drafts.append(
                FindingDraft(
                    kind="runner_queue_wait",
                    module="waste",
                    # Waits past an hour hold back every result on that event.
                    severity=3 if p90 >= 3600 else 2,
                    confidence=0.9,
                    dedupe_key=f"runner_queue_wait:{event}",
                    title=(
                        f"`{event}` runs wait p90 {_mins(p90)} before any job starts "
                        f"(max {_mins(seconds[-1])}, {long_share:.0%} of {len(seconds)} "
                        f"runs over {int(LONG_WAIT_SECONDS // 60)} min) in {names}"
                    ),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=_suggested(kind),
                    savings=None,
                    evidence=[
                        EvidenceDraft(
                            kind="run_history",
                            run_ids=[run_id for run_id, _ in reversed(pooled)][:MAX_CITED_RUNS],
                            payload={
                                "event": event,
                                "runs": len(seconds),
                                "wait_p50_seconds": round(percentile(seconds, 0.50), 1),
                                "wait_p90_seconds": round(p90, 1),
                                "wait_max_seconds": round(seconds[-1], 1),
                                "share_over_long_wait": round(long_share, 3),
                                "long_wait_seconds": LONG_WAIT_SECONDS,
                                "runner_kind": kind or "unknown",
                                "measure": "first job queued to first job started",
                                "workflows": [
                                    {
                                        "workflow": p.workflow,
                                        "runs": len(p.waits),
                                        "wait_p50_seconds": round(p.p50, 1),
                                        "wait_p90_seconds": round(p.p90, 1),
                                        "wait_max_seconds": round(p.max, 1),
                                        "runner_kind": p.runner_kind or "unknown",
                                        "runs_excluded_implausible_wait": p.excluded,
                                    }
                                    for p in profs
                                ],
                            },
                        ),
                    ],
                )
            )
        return drafts
