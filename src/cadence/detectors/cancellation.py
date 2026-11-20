"""Missing `concurrency: cancel-in-progress`.

The cleanest finding in the catalog: detection is a config read, the waste is measured by
exact replay (we know precisely which runs overlapped and by how much), and the fix is
three lines of YAML that change no build semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RunObservation
from cadence.simulate import find_superseded_runs, replay_cancellation_savings

DETECTOR_ID = "waste.no_run_cancellation"
# @2: titles, suggested actions and evidence now describe the workflow's actual concurrency
# state instead of asserting "no cancel-in-progress" for all of them (CAVEATS 55). Bumped
# because persisted findings carry the old wording and must stay attributable to it.
#
# @3: a conditional `cancel-in-progress` of the exact form
# `${{ github.event_name == '<event>' }}` (or `!=`) is now evaluated against each
# superseding run's event. Overlaps the condition already decides -- deliberately exempted
# or already cancelled -- are not claimed as recoverable (CAVEATS 81).
DETECTOR_VERSION = "no_run_cancellation@3"

SUGGESTED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true"""

# A run reported as spanning longer than this was not executing continuously, so treating
# its whole span as cancellable compute is wrong.
#
# GitHub's partial re-run is why. "Re-run failed jobs" carries the *successful* jobs
# forward into the new attempt with their original timestamps, so one attempt can contain
# jobs days apart: sveltejs/kit run 33123664062 has attempt 2 holding a job started
# 2026-08-27 alongside one completed 2026-08-31. Nothing is wrong with the ingest -- that
# is what the API reports -- but the run's span is then 3.76 days of mostly nothing.
#
# Unbounded, that single run contributed 323,340 of its repo's 325,025 wasted seconds and
# drove the reported recoverable share to 5,132%. Excluded rather than clamped: a clamp
# would invent a number for a run we cannot measure, and the count of exclusions is
# reported as evidence instead.
MAX_PLAUSIBLE_RUN_SECONDS = 24 * 3600.0


@dataclass(frozen=True, slots=True)
class CancellationState:
    """What a workflow's concurrency config actually says, so the finding can say it too.

    The title used to read *"no cancel-in-progress in ci.yml"* for every workflow the
    detector fired on. For 25 corpus workflows that was false — they cancel via an
    expression, most often `${{ github.event_name == 'pull_request' }}` — and a maintainer
    can check it in ten seconds (CAVEATS 55). A false claim a reader can verify that fast
    costs trust in every other number on the page.

    **The parser still does not evaluate expressions**, deliberately: guessing at GitHub's
    semantics is worse than naming what we cannot evaluate. What changes is the wording —
    each state is described as what it is.
    """

    kind: str          # absent | group_only | explicit_false | conditional | unrecognised
    expression: str | None = None

    def title(self, superseded: int, path: str) -> str:
        if self.kind == "conditional":
            return (
                f"{superseded} superseded runs still finished — cancel-in-progress in "
                f"{path} is conditional"
            )
        if self.kind == "explicit_false":
            return (
                f"{superseded} superseded runs finished anyway — cancel-in-progress is "
                f"set to false in {path}"
            )
        if self.kind == "group_only":
            return (
                f"{superseded} superseded runs finished anyway — concurrency in {path} "
                f"groups runs but does not cancel them"
            )
        if self.kind == "unrecognised":
            return (
                f"{superseded} superseded runs finished anyway — cancel-in-progress in "
                f"{path} has a value we could not interpret"
            )
        return f"{superseded} superseded runs finished anyway — no cancel-in-progress in {path}"

    def suggested(self) -> str:
        if self.kind == "conditional":
            return (
                f"`cancel-in-progress: {self.expression}` leaves some superseded runs "
                "running to completion — usually pushes to the default branch, which the "
                "condition excludes. If those runs are not needed, widen the condition or "
                "set it to `true`. If they are needed, suppress this finding with that "
                "reason."
            )
        if self.kind == "explicit_false":
            return (
                "`cancel-in-progress: false` is set explicitly. If that is deliberate — for "
                "runs that must always finish — suppress this finding with the reason. "
                "Otherwise set it to `true`."
            )
        if self.kind == "group_only":
            return (
                "A concurrency group already exists but only queues runs; add "
                "`cancel-in-progress: true` to it so a newer run cancels the one it replaces."
            )
        if self.kind == "unrecognised":
            return (
                "Set `cancel-in-progress: true`, or confirm the current value does what you "
                "intend."
            )
        return SUGGESTED

    @property
    def evidence(self) -> dict[str, str]:
        """What the config says, stated positively. The old payload said
        `{"missing": ...}` even when the key was present."""
        if self.kind == "conditional":
            return {"cancel_in_progress": "conditional", "expression": self.expression or ""}
        if self.kind == "explicit_false":
            return {"cancel_in_progress": "false"}
        if self.kind == "group_only":
            return {"cancel_in_progress": "absent", "concurrency": "group only"}
        if self.kind == "unrecognised":
            return {"cancel_in_progress": "unrecognised", "value": self.expression or ""}
        return {"missing": "concurrency.cancel-in-progress"}


# The one expression shape evaluated: `github.event_name` compared with a single literal.
# GitHub expression literals are single-quoted only. Anything else -- `github.ref`, `&&`,
# `contains(...)`, a reversed operand order -- stays unevaluated, because a wrong guess
# at GitHub's semantics is worse than naming what we cannot evaluate.
_EVENT_CONDITION = re.compile(
    r"^\$\{\{\s*github\.event_name\s*(==|!=)\s*'([A-Za-z_]+)'\s*\}\}$"
)


@dataclass(frozen=True, slots=True)
class EventCondition:
    """`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`, parsed."""

    op: str      # == or !=
    event: str

    def cancels(self, event: str | None) -> bool | None:
        """Whether a run triggered by `event` cancels its group's in-progress run.

        GitHub evaluates `cancel-in-progress` in the context of the run that *joins* the
        group, so this takes the superseding run's event. None when the event is unknown.
        GitHub compares strings case-insensitively.
        """
        if event is None:
            return None
        same = event.lower() == self.event.lower()
        return same if self.op == "==" else not same


def parse_event_condition(expression: str | None) -> EventCondition | None:
    if not expression:
        return None
    m = _EVENT_CONDITION.match(expression.strip())
    return EventCondition(op=m.group(1), event=m.group(2)) if m else None


def cancellation_state(concurrency: dict[str, Any] | None) -> CancellationState:
    """Classify a workflow's concurrency config. Never evaluates an expression."""
    if concurrency is None:
        return CancellationState("absent")
    if "cancel-in-progress" not in concurrency:
        return CancellationState("group_only")
    value = concurrency["cancel-in-progress"]
    if value is False:
        return CancellationState("explicit_false")
    if isinstance(value, str) and "${{" in value:
        return CancellationState("conditional", expression=value.strip())
    # `true` never reaches here: the detector skips workflows that already cancel. Anything
    # else — a quoted "true", a number — is named as unrecognised rather than guessed at.
    return CancellationState("unrecognised", expression=repr(value))


class NoRunCancellationDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []

        for wf in ctx.workflows:
            if wf.parse_error or not wf.jobs:
                continue
            if wf.cancel_in_progress:
                continue  # already correct -- say nothing

            # Scope to this workflow's own runs. `concurrency` is declared per file, so
            # a run superseded under workflow A says nothing about workflow B -- and
            # attributing repo-wide waste to every file would multiply one pool of
            # wasted seconds by the number of workflows.
            wf_runs = [r for r in ctx.runs if r.workflow_path == wf.path]
            if not wf_runs:
                continue

            # exec_started_epoch, not started_epoch. The saving here is compute that a
            # superseded run consumed after it should have been killed, and a queued run
            # consumes nothing. Using queue entry made a run that sat four days before
            # executing appear to overlap every other run in that window: sveltejs/kit run
            # 33123664062 executed for 79 seconds and contributed 323,357 seconds of
            # "waste", 98% of that repo's reported total, inflating its recoverable share
            # to 5,132%.
            usable = [
                r
                for r in wf_runs
                # `is not None`, not truthiness: an epoch or duration of 0 is a real
                # value, and testing it as a boolean silently drops those rows.
                if r.head_branch
                and r.exec_started_epoch is not None
                and r.completed_epoch is not None
                and (r.completed_epoch - r.exec_started_epoch) <= MAX_PLAUSIBLE_RUN_SECONDS
            ]
            excluded = len(wf_runs) - len(usable)

            # The filter above already proved these are non-None; the comprehension
            # restates it so the types match find_superseded_runs' signature.
            superseded = find_superseded_runs(
                [
                    (r.run_id, str(r.head_branch), float(r.exec_started_epoch or 0.0),
                     float(r.completed_epoch or 0.0))
                    for r in usable
                ]
            )
            if not superseded:
                continue

            state = cancellation_state(wf.concurrency)

            # A recognised condition has already decided every overlap whose superseding
            # run's event we know. Where it says "do not cancel", the overlap is the
            # author's stated policy -- typically each push to main must report its own
            # result. Where it says "cancel" and the older run finished anyway, the two
            # runs were not in the same group (a group keyed on the sha, say), so editing
            # `cancel-in-progress` recovers nothing. Neither is claimable; both are counted
            # in the evidence rather than dropped silently.
            decided = 0
            condition = parse_event_condition(state.expression)
            if state.kind == "conditional" and condition is not None:
                event_of = {r.run_id: r.event for r in usable}
                undecided = [
                    s for s in superseded
                    if condition.cancels(event_of.get(s.superseded_by)) is None
                ]
                decided = len(superseded) - len(undecided)
                superseded = undecided
                if not superseded:
                    continue

            savings = replay_cancellation_savings(superseded, total_runs=len(usable))
            if savings is None or savings.seconds_per_run <= 0:
                continue

            wasted_run_ids = [s.run_id for s in superseded][:50]
            # Superseded runs burn every job that was still executing, not one job's
            # worth of elapsed time, so the billed multiplier is the run's job count.
            avg_jobs = _avg_job_count(wf_runs)

            drafts.append(
                FindingDraft(
                    kind="no_run_cancellation",
                    module="waste",
                    severity=3,
                    confidence=0.95,
                    dedupe_key=f"no_run_cancellation:{wf.path}",
                    title=state.title(len(superseded), wf.path),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=state.suggested(),
                    savings=savings,
                    parallel_jobs=avg_jobs,
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=1,
                            line_end=1,
                            payload=state.evidence,
                        ),
                        EvidenceDraft(
                            kind="run_history",
                            run_ids=wasted_run_ids,
                            payload={
                                "superseded_count": len(superseded),
                                "total_runs": len(usable),
                                # Runs whose reported span was too long to be one
                                # continuous execution, usually a partial re-run carrying
                                # older jobs forward. Surfaced rather than silently
                                # dropped: a reader deserves to know the sample shrank.
                                "runs_excluded_implausible_span": excluded,
                                # Overlaps a recognised event condition already decides,
                                # either as a deliberate exemption or as runs in
                                # different groups. Not recoverable by this edit.
                                "overlaps_decided_by_condition": decided,
                                "workflow": wf.path,
                                "detail": savings.detail,
                            },
                        ),
                        EvidenceDraft(
                            kind="counterfactual",
                            payload={
                                "basis": savings.basis.value,
                                "seconds_per_run": round(savings.seconds_per_run, 2),
                                "method": "exact overlap of superseded run with its successor",
                            },
                        ),
                    ],
                )
            )
        return drafts


def _avg_job_count(runs: list[RunObservation]) -> float:
    """Average concurrent jobs, over one workflow's runs — the billed multiplier."""
    counts = [len(r.timings) for r in runs if r.timings]
    return (sum(counts) / len(counts)) if counts else 1.0
