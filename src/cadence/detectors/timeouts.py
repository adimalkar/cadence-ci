"""Jobs that hung until their timeout killed them.

GitHub records a job stopped by its `timeout-minutes` as `cancelled`, the same conclusion
as one a person or a concurrency group cancelled, and its re-run usually passes. So the
hang is invisible twice over: the failure rules read only `failure`, and the latest
attempt is green. What gives it away is the duration: a cancelled job whose runtime is
its configured limit was killed by it.

Found by a backtest on a private field repository. One step hung at an apt install until
the 35-minute job timeout four times in a day, then eight more the next, each re-run
passing, before the team put a step-level timeout on it. The audit as of the morning
after the first four said nothing, because no rule could see them.

No savings figure is claimed. The burned runner time is measured and reported, but what
a fix recovers depends on whether the hang is removed or merely cut short.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, CancelledJob
from cadence.workflow import Job, Workflow

DETECTOR_ID = "flake.timeout_hang"
DETECTOR_VERSION = "timeout_hang@1"

MIN_KILLS = 2
MANY_KILLS = 5
# A kill lands shortly after the limit: observed 18-24 s past a 35-minute timeout. A job
# that stopped well before its limit was cancelled by something else.
EARLY_TOLERANCE_SECONDS = 30.0
LATE_TOLERANCE_SECONDS = 300.0
MAX_CITED = 50


@dataclass(slots=True)
class HangGroup:
    workflow: Workflow
    job: Job
    step: str | None
    limit_minutes: float
    kills: list[CancelledJob] = field(default_factory=list)

    @property
    def passed_on_rerun(self) -> int:
        """Killed runs whose latest attempt is later than the kill and succeeded."""
        runs = {k.run_id: k for k in self.kills}
        return sum(1 for k in runs.values()
                   if k.run_attempt > k.attempt and k.run_conclusion == "success")

    @property
    def runner_minutes(self) -> float:
        return sum(k.exec_seconds for k in self.kills) / 60.0


def killed_by_timeout(exec_seconds: float, limit_minutes: float) -> bool:
    limit = limit_minutes * 60.0
    return limit - EARLY_TOLERANCE_SECONDS <= exec_seconds <= limit + LATE_TOLERANCE_SECONDS


def group_hangs(ctx: AuditContext) -> list[HangGroup]:
    by_path = {wf.path: wf for wf in ctx.workflows if not wf.parse_error}
    groups: dict[tuple[str, str, str | None], HangGroup] = {}
    for c in ctx.cancelled_jobs:
        wf = by_path.get(c.workflow_path or "")
        if wf is None:
            continue
        job = wf.job_for_runtime_name(c.job_name, c.name_base)
        if job is None:
            continue
        limit = job.timeout_minutes
        if limit is None or not killed_by_timeout(c.exec_seconds, limit):
            continue
        key = (wf.path, job.key, c.step_name)
        group = groups.get(key)
        if group is None:
            group = groups[key] = HangGroup(wf, job, c.step_name, limit)
        group.kills.append(c)
    return sorted(groups.values(),
                  key=lambda g: (-len(g.kills), g.workflow.path, g.job.key, g.step or ""))


class TimeoutHangDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        return [self._finding(g) for g in group_hangs(ctx) if len(g.kills) >= MIN_KILLS]

    def _finding(self, g: HangGroup) -> FindingDraft:
        n = len(g.kills)
        limit = f"{g.limit_minutes:g}-minute"
        where = f"`{g.step}` in `{g.job.key}`" if g.step else f"`{g.job.key}`"
        default = "timeout-minutes" not in g.job.raw
        reran = g.passed_on_rerun
        distinct_runs = len({k.run_id for k in g.kills})
        title = (
            f"{where} ({g.workflow.path}) hung until the {limit} job timeout {n} times"
            f" ({g.runner_minutes:.0f} runner-minutes)"
            + (f"; {reran} of {distinct_runs} runs passed on re-run" if reran else "")
        )
        action = (
            f"Give the step its own `timeout-minutes`, a little above its normal duration, so "
            f"a hang fails in minutes instead of holding the runner for the job's "
            f"{g.limit_minutes:g}. Then find why it hangs."
        )
        if reran:
            action += (" Re-runs passing points at the environment (network, package mirrors, "
                       "locks, a cold cache) rather than the code.")
        if default:
            action += (" The job declares no `timeout-minutes`, so GitHub's 360-minute "
                       "default applied.")
        return FindingDraft(
            kind="timeout_hang",
            module="flake",
            severity=4 if n >= MANY_KILLS else 3,
            # The duration match is measured; that the timeout, rather than a person
            # cancelling at that exact minute, ended the job is inferred.
            confidence=0.9,
            dedupe_key=f"timeout_hang:{g.workflow.path}:{g.job.key}:{g.step or ''}",
            title=title,
            detector_version=DETECTOR_VERSION,
            suggested_action=action,
            savings=None,
            evidence=[
                EvidenceDraft(
                    kind="code_range",
                    file_path=g.workflow.path,
                    line_start=g.job.line,
                    line_end=g.job.line,
                    payload={"job": g.job.key, "timeout_minutes": g.limit_minutes,
                             "timeout_declared": not default},
                ),
                EvidenceDraft(
                    kind="run_history",
                    file_path=g.workflow.path,
                    run_ids=sorted({k.run_id for k in g.kills})[:MAX_CITED],
                    payload={
                        "step": g.step,
                        "kills": n,
                        "runs_passed_on_rerun": reran,
                        "runner_minutes": round(g.runner_minutes, 1),
                        "attempts": [
                            {"run_id": k.run_id, "attempt": k.attempt,
                             "job_seconds": round(k.exec_seconds, 1),
                             "step_seconds": (None if k.step_seconds is None
                                              else round(k.step_seconds, 1))}
                            for k in sorted(g.kills, key=lambda k: (k.run_id, k.attempt))
                        ][:MAX_CITED],
                    },
                ),
            ],
        )
