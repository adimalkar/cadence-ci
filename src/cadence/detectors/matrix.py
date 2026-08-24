"""Class C — matrix legs that never caught anything on their own.

Matrix explosion is the single most-cited waste pattern in every CI cost writeup found
during research, and one report attributes a $4,200/month bill largely to a matrix that
grew to six Node versions unnoticed. This rule is the reason the phase pays for itself.

The claim is deliberately narrow and falsifiable: *this leg has never been the **sole**
failure*. A leg that only ever fails alongside others has never been the thing that
caught a bug — the others would have caught it too. That is very different from "this leg
never fails", which would be an argument for deleting your passing tests.
"""

from __future__ import annotations

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext
from cadence.simulate import Savings, SavingsBasis
from cadence.workflow import Workflow, strip_matrix_suffix

DETECTOR_ID = "waste.non_discriminating_matrix_leg"
DETECTOR_VERSION = "non_discriminating_matrix_leg@1"

# Never recommend removing a leg on thin evidence. The design doc sets ~200 runs; we
# scope per workflow, so this is runs of the workflow the leg belongs to.
MIN_RUNS = 150
# A leg that has never failed *at all* is a different (and much weaker) claim than one
# that has failed only in company, so require some observed failures in the workflow
# before concluding anything about discrimination.
MIN_WORKFLOW_FAILURES = 5


class NonDiscriminatingMatrixLegDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []
        for wf in ctx.workflows:
            if wf.parse_error or not wf.jobs:
                continue
            drafts.extend(self._for_workflow(ctx, wf))
        return drafts

    def _for_workflow(self, ctx: AuditContext, wf: Workflow) -> list[FindingDraft]:
        out: list[FindingDraft] = []
        wf_runs = [r for r in ctx.runs if r.workflow_path == wf.path]
        if len(wf_runs) < MIN_RUNS:
            return out

        legs = ctx.leg_outcomes.get(wf.path)
        if not legs:
            return out

        failing_runs = {
            run_id
            for outcomes in legs.values()
            for run_id, conclusion in outcomes
            if conclusion == "failure"
        }
        if len(failing_runs) < MIN_WORKFLOW_FAILURES:
            return out

        # For each run, which legs failed. A leg is discriminating if it was ever the
        # only one to fail.
        failures_by_run: dict[int, set[str]] = {}
        for leg, outcomes in legs.items():
            for run_id, conclusion in outcomes:
                if conclusion == "failure":
                    failures_by_run.setdefault(run_id, set()).add(leg)

        sole_failures: dict[str, int] = {}
        for _run_id, failed_legs in failures_by_run.items():
            if len(failed_legs) == 1:
                only = next(iter(failed_legs))
                sole_failures[only] = sole_failures.get(only, 0) + 1

        for leg, outcomes in sorted(legs.items()):
            if sole_failures.get(leg, 0) > 0:
                continue  # it has caught something alone; leave it be
            runs_seen = len({run_id for run_id, _ in outcomes})
            if runs_seen < MIN_RUNS:
                continue

            # `leg` is the verbatim runtime name; the matcher's fallback expects the
            # stripped form as name_base.
            job = wf.job_for_runtime_name(leg, strip_matrix_suffix(leg))
            if job is None or job.matrix_leg_count < 2:
                continue  # only meaningful for an actual matrix

            savings = self._leg_savings(ctx, wf.path, leg, runs_seen)
            if savings is None:
                continue

            failed_together = sum(1 for _, c in outcomes if c == "failure")
            out.append(
                FindingDraft(
                    kind="non_discriminating_matrix_leg",
                    module="waste",
                    severity=2,
                    # Deliberately modest: coverage is a maintainer's judgement call, and
                    # "never caught anything alone" is evidence, not proof of redundancy.
                    confidence=0.65,
                    dedupe_key=f"non_discriminating_leg:{wf.path}:{leg}",
                    title=(
                        f"Matrix leg `{leg}` has never been the sole failure "
                        f"in {runs_seen} runs"
                    ),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=(
                        f"Consider moving `{leg}` to a nightly or merge-only run. It "
                        f"failed {failed_together} time(s), always alongside another leg "
                        f"that would have caught the same problem. This is a coverage "
                        f"decision — the data says it has not paid for itself on PRs."
                    ),
                    savings=savings,
                    parallel_jobs=1.0,
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=job.line,
                            line_end=job.line,
                            payload={"job": job.key, "leg": leg},
                        ),
                        EvidenceDraft(
                            kind="run_history",
                            run_ids=[r for r, _ in outcomes][:50],
                            payload={
                                "runs_observed": runs_seen,
                                "failures": failed_together,
                                "sole_failures": 0,
                            },
                        ),
                        EvidenceDraft(
                            kind="counterfactual",
                            payload={
                                "basis": savings.basis.value,
                                "seconds_per_run": round(savings.seconds_per_run, 2),
                                "method": "billable time of this leg, removed from PR runs",
                            },
                        ),
                    ],
                )
            )
        return out

    @staticmethod
    def _leg_savings(
        ctx: AuditContext, workflow_path: str, leg: str, runs_seen: int
    ) -> Savings | None:
        """Billable seconds this leg costs per run.

        Replay, not projection: we observed exactly what the leg took. Note this is
        *billable* time rather than wall clock — removing one leg of a parallel matrix
        usually does not shorten the run at all, so claiming wall-clock savings would be
        wrong. The saving is compute, and the report says so.
        """
        durations = ctx.leg_durations.get((workflow_path, leg))
        if not durations or len(durations) < 20:
            return None
        durations = sorted(durations)
        median = durations[len(durations) // 2]
        if median < 10.0:
            return None
        return Savings(
            seconds_per_run=median,
            basis=SavingsBasis.REPLAY,
            low=median,
            high=median,
            n_runs=len(durations),
            detail=(
                f"leg p50 {int(median // 60)}:{int(median % 60):02d} billable across "
                f"{len(durations)} observations (compute, not wall clock)"
            ),
        )
