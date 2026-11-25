"""Jobs GitHub refused to run: failed without executing a single step.

A job that fails before its first step was not broken by the code. GitHub declined to
run it -- a billing block or spending limit on hosted runners, a label no runner serves --
and says why only in a check-run annotation. Every rule that reads failing steps is
blind to it, and a re-run fails the same way until the cause clears.

Found by a backtest in field testing, where no existing rule reported such jobs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, UnstartedFailure

DETECTOR_ID = "observability.jobs_not_started"
DETECTOR_VERSION = "jobs_not_started@1"

MIN_REFUSED = 5
MANY_REFUSED = 50
MAX_CITED = 50


def runner_kind(labels: tuple[str, ...]) -> str:
    return "self-hosted" if any(lb.lower() == "self-hosted" for lb in labels) else "hosted"


def refusal_groups(failures: list[UnstartedFailure]) -> list[list[UnstartedFailure]]:
    """Refused jobs grouped by runner kind and label set, largest group first.

    A refusal is about where a job asked to run, not which workflow asked: a billing block
    refuses every hosted job alike, and a missing runner refuses one label.
    """
    groups: dict[tuple[str, tuple[str, ...]], list[UnstartedFailure]] = {}
    for u in failures:
        labels = tuple(sorted(u.runner_labels))
        groups.setdefault((runner_kind(labels), labels), []).append(u)
    return sorted(groups.values(), key=lambda g: (-len(g), sorted(g[0].runner_labels)))


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d")


class JobsNotStartedDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        return [self._finding(ctx, g) for g in refusal_groups(ctx.unstarted_failures)
                if len(g) >= MIN_REFUSED]

    def _finding(self, ctx: AuditContext, group: list[UnstartedFailure]) -> FindingDraft:
        labels = tuple(sorted(group[0].runner_labels))
        kind = runner_kind(labels)
        first = min(u.created_epoch for u in group)
        last = max(u.created_epoch for u in group)
        span = _day(first) if _day(first) == _day(last) else f"{_day(first)} to {_day(last)}"
        reasons = sorted({msg for u in group for msg in ctx.job_annotations.get(u.job_id, [])})
        looked_up = any(u.job_id in ctx.job_annotations for u in group)
        runs = sorted({u.run_id for u in group})
        workflows = sorted({u.workflow_path for u in group if u.workflow_path})
        label_text = ", ".join(f"`{lb}`" for lb in labels) or "no recorded labels"

        title = (f"{len(group)} jobs on {kind} runners ({label_text}) failed without running "
                 f"a step, {span}")
        if reasons:
            title += f": GitHub says \"{reasons[0].split('. ')[0].rstrip('.')}\""
        action = (
            "A job that fails before its first step was refused, not broken, and re-running "
            "it fails the same way until the cause clears."
        )
        if reasons:
            action += f" GitHub's reason: {reasons[0]}"
        elif kind == "hosted":
            action += (" On hosted runners this is most often a billing block or spending "
                       "limit; GitHub states the reason in the job's annotations.")
        else:
            action += (" On self-hosted runners, check that a runner with these labels is "
                       "registered and online; GitHub states the reason in the job's "
                       "annotations.")
        return FindingDraft(
            kind="jobs_not_started",
            module="observability",
            severity=4 if len(group) >= MANY_REFUSED else 3,
            # Zero executed steps on a failed job is observed; the cause is GitHub's own
            # annotation when fetched, otherwise unknown.
            confidence=0.95 if reasons else 0.8,
            dedupe_key=f"jobs_not_started:{kind}:{','.join(labels)}",
            title=title,
            detector_version=DETECTOR_VERSION,
            suggested_action=action,
            savings=None,
            evidence=[
                EvidenceDraft(
                    kind="run_history",
                    run_ids=runs[:MAX_CITED],
                    payload={
                        "runner_kind": kind,
                        "runner_labels": list(labels),
                        "jobs": len(group),
                        "runs": len(runs),
                        "workflows": workflows,
                        "first": _day(first),
                        "last": _day(last),
                        "reason_looked_up": looked_up,
                        "reasons": reasons,
                        "job_ids": sorted(u.job_id for u in group)[:MAX_CITED],
                    },
                ),
            ],
        )
