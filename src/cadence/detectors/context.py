"""What a detector gets handed: parsed config plus observed history, already joined.

Assembling this once and passing it to every detector keeps detectors pure and stops each
one re-querying the database with slightly different filters -- which is how two rules end
up disagreeing about the same repo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from cadence.cost import CostContext
from cadence.dag import NodeTiming
from cadence.workflow import Step, Workflow

_EXPRESSION = re.compile(r"\$\{\{")


def step_runtime_name(step: Step) -> str | None:
    """The name GitHub records for a step, where config alone can say.

    `name:` verbatim; for an unnamed `run:` step, `Run ` plus the script's first line; for
    an unnamed `uses:` step, `Run ` plus the reference. None when the name is an expression,
    since the recorded value depends on the run.
    """
    if step.name:
        return None if _EXPRESSION.search(step.name) else step.name
    if step.run:
        first = next((ln.strip() for ln in step.run.splitlines() if ln.strip()), "")
        return f"Run {first}" if first else None
    if step.uses:
        return f"Run {step.uses}"
    return None


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
class JobFailure:
    """One failed job, reduced to where it first went wrong.

    `step_name` is the first step in the job with a failing conclusion, by step number.
    Steps run in order and a failing step normally ends the job, so the first failure is
    the cause and everything after it is either skipped or an `if: always()` cleanup.
    """

    run_id: int
    job_name: str
    step_name: str
    step_number: int
    # Seconds the job ran before it was abandoned. Not recoverable -- the work was real --
    # but it is what makes one failure more expensive than another.
    job_seconds: float


# setup-node's own test, copied from its src/main.ts: "npm", "npm@…", "^npm@…".
_NPM_PACKAGE_MANAGER = re.compile(r"^(\^)?npm(@.*)?$")


@dataclass(frozen=True, slots=True)
class RootPackageJson:
    """The repository root's `package.json`, as far as a detector needs it.

    Three states, because "we did not look" and "it is not there" lead to opposite answers:
    from v5, `actions/setup-node` caches npm on its own when this file names npm as the
    package manager, and not otherwise (CAVEATS 59). Unfetched is the default so a caller
    that never asks gets the old, conservative behaviour.
    """

    fetched: bool = False
    # Fetched and None: the file does not exist. Unparseable JSON is `{}` -- setup-node
    # swallows the parse error and caches nothing, and so do we.
    data: dict[str, Any] | None = None

    @classmethod
    def from_text(cls, text: str | None) -> RootPackageJson:
        if text is None:
            return cls(fetched=True, data=None)
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = {}
        return cls(fetched=True, data=parsed if isinstance(parsed, dict) else {})

    def declares_npm(self) -> bool:
        """setup-node's `getNameFromPackageManagerField`: `devEngines.packageManager`
        (an object or a list of them) first, then the top-level `packageManager`."""
        if not self.data:
            return False
        dev = (self.data.get("devEngines") or {})
        dev_pm = dev.get("packageManager") if isinstance(dev, dict) else None
        for entry in dev_pm if isinstance(dev_pm, list) else [dev_pm] if dev_pm else []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str) \
                    and _NPM_PACKAGE_MANAGER.match(entry["name"]):
                return True
        top = self.data.get("packageManager")
        return isinstance(top, str) and bool(_NPM_PACKAGE_MANAGER.match(top))


@dataclass(slots=True)
class AuditContext:
    repo_id: int
    owner: str
    name: str
    is_private: bool
    workflows: list[Workflow]
    runs: list[RunObservation]
    # Keyed by (job name_base, step name), as recorded: the display name, merged across
    # every workflow that has a job by that name. Right for ranking (long tail); wrong for
    # asking about one step in one config job -- use `series_for_step` for that.
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
    # Failed jobs with the step they first failed at. Empty for a repo whose runs all
    # passed, which is a legitimate state and not a coverage problem.
    failures: list[JobFailure] = field(default_factory=list)
    # Read only when a workflow runs setup-node without `cache:`; see RootPackageJson.
    root_package_json: RootPackageJson = field(default_factory=RootPackageJson)
    # The same step durations, resolved to config: (workflow path, job key, step name).
    # Resolution uses `Workflow.job_for_runtime_name`, the mapping the run DAG uses, within
    # the run's own workflow -- so `build` in ci.yml and `build` in release.yml stay apart,
    # and a job with `name: Build` is found under its key `build` (CAVEATS 61).
    step_series_resolved: dict[tuple[str, str, str], StepSeries] = field(default_factory=dict)
    # NOTE: class E (runner fit) is deliberately NOT built. Detecting "single-threaded
    # job on an 8-core runner" needs CPU utilisation, which the Actions API does not
    # expose -- only labels. Inferring it from duration alone would be a guess presented
    # as a measurement, so the rule is left out rather than approximated.

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def series_for_step(self, workflow_path: str, job_key: str, step: Step) -> StepSeries | None:
        """Observed durations of exactly this config step, or None.

        An unnamed `run:` step is recorded as `Run <first line of the script>`, which is
        GitHub's default. There is deliberately no fallback to another step: a series from
        a different step is not evidence about this one (CAVEATS 61).
        """
        name = step_runtime_name(step)
        if name is None:
            return None
        return self.step_series_resolved.get((workflow_path, job_key, name))

    def runs_for_workflow(self, path: str) -> list[RunObservation]:
        return [r for r in self.runs if r.timings]

    def edges_for(self, wf: Workflow) -> dict[str, list[str]]:
        """`needs:` graph for a workflow, with dangling edges dropped.

        A `needs:` naming a job that does not exist would otherwise make the graph
        untopologisable and silently disable critical-path analysis for the whole repo.
        """
        keys = set(wf.jobs)
        return {k: [d for d in j.needs if d in keys] for k, j in wf.jobs.items()}
