"""Commits on the default branch that never got a CI result of their own.

A concurrency group shared by every push to the default branch -- `group: ci-${{
github.ref }}` -- makes each new push displace the run before it: with
`cancel-in-progress` the running one is cancelled, and even without it GitHub keeps one
pending run per group and cancels the older pending run when a third arrives. Under merge
traffic, commits are dropped rather than delayed. A regression in a dropped commit is
then reported against whichever later commit's run finishes, which is exactly where a
bisect starts looking in the wrong place, and a cancelled run shows as neither red nor
green.

This is the opposite failure to the one `no_run_cancellation` looks for: cancelling
superseded runs saves minutes on a pull request, where only the newest commit matters,
and loses information on the default branch, where every commit is history. Found by a
backtest: on a private field repository, 22 default-branch commits lost their CI result
this way over 18 days before the team noticed through a misattributed failure.

No savings figure. This costs correctness, not minutes, and the fix spends minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RunOutcome
from cadence.workflow import Workflow

DETECTOR_ID = "observability.untested_default_branch_commits"
DETECTOR_VERSION = "untested_default_branch_commits@1"

MIN_UNTESTED = 3
MANY_UNTESTED = 10
# A cancelled run counts as displaced only if a newer default-branch run of the same
# workflow had already been created when it ended. GitHub records the cancellation a few
# seconds after the newer run appears; a manual cancellation has no such successor.
SUPERSEDE_SLACK_SECONDS = 60.0
# Expressions that make a group unique per commit or per run: such a group cannot evict.
_PER_COMMIT_KEYS = ("github.sha", "github.run_id", "github.run_number", "github.run_attempt")
MAX_CITED = 50


@dataclass(frozen=True, slots=True)
class UntestedCommit:
    sha: str
    cancelled_run_ids: tuple[int, ...]
    displaced_by: int


def group_is_shared(wf: Workflow) -> bool:
    """True when the workflow-level concurrency group can hold two different commits."""
    if not wf.concurrency:
        return False
    group = str(wf.concurrency.get("group") or "")
    return bool(group) and not any(key in group for key in _PER_COMMIT_KEYS)


def untested_commits(outcomes: list[RunOutcome], workflow_path: str,
                     default_branch: str) -> tuple[list[UntestedCommit], int]:
    """Default-branch push commits whose every run of this workflow was displaced.

    Returns them with the number of distinct commits considered. A commit with a run still
    in progress is not judged; one with any concluded, non-cancelled run was tested.
    """
    runs = sorted(
        (o for o in outcomes
         if o.workflow_path == workflow_path and o.event == "push"
         and o.head_branch == default_branch and o.head_sha),
        key=lambda o: (o.created_epoch, o.run_id),
    )
    by_sha: dict[str, list[RunOutcome]] = {}
    for o in runs:
        by_sha.setdefault(o.head_sha or "", []).append(o)

    def successor(o: RunOutcome) -> RunOutcome | None:
        ended = o.updated_epoch if o.updated_epoch is not None else o.created_epoch
        for later in runs:
            if later.head_sha != o.head_sha and later.created_epoch > o.created_epoch \
                    and later.created_epoch <= ended + SUPERSEDE_SLACK_SECONDS:
                return later
        return None

    out: list[UntestedCommit] = []
    for sha, sha_runs in by_sha.items():
        if any(o.conclusion != "cancelled" for o in sha_runs):
            continue  # tested, skipped by design, or still running
        displaced = [(o, successor(o)) for o in sha_runs]
        if all(nxt is not None for _, nxt in displaced):
            last = displaced[-1][1]
            assert last is not None
            out.append(UntestedCommit(
                sha=sha,
                cancelled_run_ids=tuple(o.run_id for o, _ in displaced),
                displaced_by=last.run_id,
            ))
    return out, len(by_sha)


class UntestedDefaultBranchCommitsDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        if not ctx.default_branch or not ctx.run_outcomes:
            return []
        out: list[FindingDraft] = []
        for wf in sorted(ctx.workflows, key=lambda w: w.path):
            if wf.parse_error or not group_is_shared(wf):
                continue
            lost, considered = untested_commits(ctx.run_outcomes, wf.path, ctx.default_branch)
            if len(lost) < MIN_UNTESTED:
                continue
            out.append(self._finding(ctx, wf, lost, considered))
        return out

    def _finding(self, ctx: AuditContext, wf: Workflow, lost: list[UntestedCommit],
                 considered: int) -> FindingDraft:
        assert wf.concurrency is not None
        branch = ctx.default_branch
        group = str(wf.concurrency.get("group"))
        cancel = wf.concurrency.get("cancel-in-progress", False)
        how = ("cancels the running one" if cancel is True
               else "replaces the pending one" if cancel in (False, None)
               else "may cancel the running one")
        cited = [rid for c in lost for rid in c.cancelled_run_ids][:MAX_CITED]
        return FindingDraft(
            kind="untested_default_branch_commits",
            module="observability",
            severity=4 if len(lost) >= MANY_UNTESTED else 3,
            # Displacement is observed exactly; that the shared group caused it, rather
            # than a person cancelling just as the next push landed, is inferred.
            confidence=0.85,
            dedupe_key=f"untested_default_branch_commits:{wf.path}",
            title=(
                f"{len(lost)} of {considered} commits pushed to `{branch}` never got a "
                f"result from {wf.path}: each run was displaced by the next push"
            ),
            detector_version=DETECTOR_VERSION,
            suggested_action=(
                f"The concurrency group `{group}` is shared by every push to `{branch}`, "
                f"so each new push {how}. A regression in a displaced commit is reported "
                f"against a later one. Key the group per commit on `{branch}` and keep "
                f"cancellation for pull requests, e.g. `group: ${{{{ github.workflow }}}}-"
                f"${{{{ github.event.pull_request.number || github.sha }}}}` with "
                f"`cancel-in-progress: ${{{{ github.event_name == 'pull_request' }}}}`."
            ),
            savings=None,
            evidence=[
                EvidenceDraft(
                    kind="code_range",
                    file_path=wf.path,
                    line_start=wf.concurrency_line or 1,
                    line_end=wf.concurrency_line or 1,
                    payload={"group": group, "cancel-in-progress": cancel},
                ),
                EvidenceDraft(
                    kind="run_history",
                    file_path=wf.path,
                    run_ids=cited,
                    payload={
                        "branch": branch,
                        "commits_considered": considered,
                        "commits_untested": len(lost),
                        "untested": [
                            {"sha": c.sha, "cancelled_runs": list(c.cancelled_run_ids),
                             "displaced_by_run": c.displaced_by}
                            for c in lost[:MAX_CITED]
                        ],
                    },
                ),
            ],
        )
