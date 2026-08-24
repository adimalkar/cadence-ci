"""Missing `concurrency: cancel-in-progress`.

The cleanest finding in the catalog: detection is a config read, the waste is measured by
exact replay (we know precisely which runs overlapped and by how much), and the fix is
three lines of YAML that change no build semantics.
"""

from __future__ import annotations

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RunObservation
from cadence.simulate import find_superseded_runs, replay_cancellation_savings

DETECTOR_ID = "waste.no_run_cancellation"
DETECTOR_VERSION = "no_run_cancellation@1"

SUGGESTED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true"""


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

            superseded = find_superseded_runs(
                [
                    (r.run_id, r.head_branch, r.started_epoch, r.completed_epoch)
                    for r in wf_runs
                    # `is not None`, not truthiness: an epoch or duration of 0 is a real
                    # value, and testing it as a boolean silently drops those rows.
                    if r.head_branch
                    and r.started_epoch is not None
                    and r.completed_epoch is not None
                ]
            )
            if not superseded:
                continue

            savings = replay_cancellation_savings(superseded, total_runs=len(wf_runs))
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
                    title=(
                        f"{len(superseded)} superseded runs finished anyway "
                        f"— no cancel-in-progress in {wf.path}"
                    ),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=SUGGESTED,
                    savings=savings,
                    parallel_jobs=avg_jobs,
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=1,
                            line_end=1,
                            payload={"missing": "concurrency.cancel-in-progress"},
                        ),
                        EvidenceDraft(
                            kind="run_history",
                            run_ids=wasted_run_ids,
                            payload={
                                "superseded_count": len(superseded),
                                "total_runs": len(wf_runs),
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
