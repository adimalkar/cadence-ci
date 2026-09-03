"""What a detector gets handed: parsed config plus observed history, already joined.

Assembling this once and passing it to every detector keeps detectors pure and stops each
one re-querying the database with slightly different filters -- which is how two rules end
up disagreeing about the same repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cadence.cost import CostContext
from cadence.dag import NodeTiming
from cadence.workflow import Workflow


@dataclass(slots=True)
class RunObservation:
    """One run, reduced to what the detectors and simulator need."""

    run_id: int
    head_branch: str | None
    # When the run's first job was *queued*. This is what a developer waits, so it is the
    # right basis for wall clock -- and the wrong basis for anything about compute.
    started_epoch: float | None
    completed_epoch: float | None
    conclusion: str | None
    # Which workflow produced this run. Load-bearing for anything scoped per workflow --
    # `concurrency` is declared per file, so repo-wide waste attributed to every file
    # would multiply the same seconds by the number of workflows.
    workflow_path: str | None = None
    head_sha: str | None = None
    # Observed jobs vs jobs we could map to a config node. Reusable workflows
    # (`jobs.x.uses: ./.github/workflows/_build.yml`) rename their jobs to
    # `x / <inner>`, which matches nothing in the calling file — so coverage can be low
    # for entirely legitimate reasons. Any figure derived from the DAG is only
    # meaningful in proportion to this.
    jobs_total: int = 0
    jobs_mapped: int = 0

    @property
    def mapping_coverage(self) -> float:
        return (self.jobs_mapped / self.jobs_total) if self.jobs_total else 0.0
    # node key -> timing, already collapsed across matrix legs
    timings: dict[str, NodeTiming] = field(default_factory=dict)

    # When the run's first job actually began *executing*. Distinct from started_epoch by
    # exactly the queue wait, which for a re-run can be days: run 33123664062 in
    # sveltejs/kit was created 2026-08-27 and started 2026-08-31, having executed for 79
    # seconds. Any rule about consumed compute must use this; a run that is queued is not
    # occupying a runner.
    exec_started_epoch: float | None = None

    @property
    def wall_seconds(self) -> float:
        if self.started_epoch is None or self.completed_epoch is None:
            return 0.0
        return max(0.0, self.completed_epoch - self.started_epoch)

    @property
    def exec_seconds(self) -> float:
        """Elapsed time this run held runners, excluding the queue wait."""
        if self.exec_started_epoch is None or self.completed_epoch is None:
            return 0.0
        return max(0.0, self.completed_epoch - self.exec_started_epoch)


@dataclass(slots=True)
class StepSeries:
    """Duration history for one named step across runs, for cache analysis."""

    job_key: str
    step_name: str
    durations: list[float] = field(default_factory=list)
    run_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class AuditContext:
    repo_id: int
    owner: str
    name: str
    is_private: bool
    workflows: list[Workflow]
    runs: list[RunObservation]
    step_series: dict[tuple[str, str], StepSeries]
    cost: CostContext
    window_days: int
    # Per-matrix-leg outcomes, keyed by workflow path: {leg_name: [(run_id, conclusion)]}.
    # Legs are the verbatim job name, since that is what distinguishes one leg from
    # another -- name_base deliberately collapses them.
    leg_outcomes: dict[str, dict[str, list[tuple[int, str | None]]]] = field(
        default_factory=dict
    )
    # (workflow_path, leg_name) -> observed execution seconds
    leg_durations: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    # Files changed per run, for the path-trigger rule: {run_id: [paths]}
    changed_paths: dict[int, list[str]] = field(default_factory=dict)
    # NOTE: class E (runner fit) is deliberately NOT built. Detecting "single-threaded
    # job on an 8-core runner" needs CPU utilisation, which the Actions API does not
    # expose -- only labels. Inferring it from duration alone would be a guess presented
    # as a measurement, so the rule is left out rather than approximated.

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def runs_for_workflow(self, path: str) -> list[RunObservation]:
        return [r for r in self.runs if r.timings]

    def edges_for(self, wf: Workflow) -> dict[str, list[str]]:
        """`needs:` graph for a workflow, with dangling edges dropped.

        A `needs:` naming a job that does not exist would otherwise make the graph
        untopologisable and silently disable critical-path analysis for the whole repo.
        """
        keys = set(wf.jobs)
        return {k: [d for d in j.needs if d in keys] for k, j in wf.jobs.items()}
