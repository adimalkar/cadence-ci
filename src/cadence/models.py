"""Provider-neutral domain types.

These are what a `CIProvider` returns. Nothing GitHub-shaped leaks past this module --
that is the entire point of the seam. A second provider (GitLab, CircleCI, Buildkite)
becomes a plugin rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(slots=True)
class Step:
    number: int
    name: str
    status: str | None
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None

    @property
    def duration(self) -> timedelta | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at


@dataclass(slots=True)
class Job:
    id: int
    run_id: int
    name: str
    status: str | None
    conclusion: str | None
    runner_labels: list[str]
    runner_group: str | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    attempt: int
    steps: list[Step] = field(default_factory=list)
    matrix: dict | None = None

    @property
    def queue_time(self) -> timedelta | None:
        """Time spent waiting for a runner.

        Kept distinct from execution time throughout. A queue-bound repo gets the
        opposite advice from a compute-bound one -- adding parallelism makes queue-bound
        pipelines slower, and being able to say so is a differentiator.
        """
        if self.created_at is None or self.started_at is None:
            return None
        return max(self.started_at - self.created_at, timedelta(0))

    @property
    def execution_time(self) -> timedelta | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at


@dataclass(slots=True)
class Run:
    id: int
    repo_id: int
    workflow_id: int | None
    workflow_path: str | None
    workflow_name: str | None
    run_number: int | None
    run_attempt: int
    event: str | None
    status: str | None
    conclusion: str | None
    head_sha: str
    head_branch: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime | None
    pull_request_number: int | None = None
    tree_sha: str | None = None
    jobs: list[Job] = field(default_factory=list)

    @property
    def wall_clock(self) -> timedelta | None:
        """Observed run duration, from the earliest job queueing to the last completion.

        Deliberately derived from jobs rather than from the run's own timestamps: the
        run-level `updated_at` includes post-processing GitHub does after the last job
        finishes, which is not time anyone can optimize away.
        """
        starts = [j.created_at or j.started_at for j in self.jobs]
        ends = [j.completed_at for j in self.jobs]
        starts = [s for s in starts if s is not None]
        ends = [e for e in ends if e is not None]
        if not starts or not ends:
            return None
        return max(ends) - min(starts)

    @property
    def billable_seconds(self) -> float:
        """Sum of job execution time. This is what a runner-minute bill is computed from.

        Note this is *not* wall clock: parallel jobs bill concurrently, so billable time
        routinely exceeds wall clock. Conflating the two is the most common error in CI
        cost analysis and produces savings claims that are wrong by the parallelism factor.
        """
        total = 0.0
        for job in self.jobs:
            if (exec_time := job.execution_time) is not None:
                total += exec_time.total_seconds()
        return total


@dataclass(slots=True)
class Repo:
    id: int
    owner: str
    name: str
    is_private: bool
    default_branch: str | None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(slots=True)
class RunPage:
    """One page of runs, plus the ETag that lets us skip re-fetching it unchanged."""

    runs: list[Run]
    etag: str | None
    not_modified: bool = False
