"""The `CIProvider` seam.

GitHub Actions is the only implementation for the first 24 weeks, and that is correct --
it dominates OSS CI and is the entire install target. The interface exists so that adding
a second provider in month eight is a plugin rather than a rewrite. One hour of design
discipline now against a month of refactoring later.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cadence.models import Job, Repo, RunPage


class RateLimited(Exception):
    """Raised when the provider asks us to back off.

    Carries the wait duration so the caller can schedule rather than spin. GitHub uses
    both primary limits (5,000/hr, resets on a clock) and secondary limits (abuse
    detection, `Retry-After`); both surface here.
    """

    def __init__(self, retry_after_seconds: float, message: str = "") -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message or f"rate limited; retry in {retry_after_seconds:.0f}s")


class NotFound(Exception):
    """Repo or resource does not exist, or the token cannot see it."""


@runtime_checkable
class CIProvider(Protocol):
    """Read-only access to a CI system's run history.

    Every method here is readable with no App install and no write scope. That is a
    deliberate constraint, not an accident of the current phase: the whole product
    thesis is that diagnosis costs the user nothing to authorize.
    """

    name: str

    async def get_repo(self, owner: str, name: str) -> Repo:
        """Resolve a repo to its provider id and metadata."""
        ...

    async def fetch_runs(
        self,
        repo: Repo,
        *,
        page: int = 1,
        per_page: int = 100,
        etag: str | None = None,
    ) -> RunPage:
        """One page of workflow runs, newest first.

        Pass the stored `etag` to get a cheap 304 when nothing changed -- conditional
        requests do not count against the rate limit, and the delta poll depends on it.
        """
        ...

    async def fetch_jobs(self, repo: Repo, run_id: int, *, attempt: int | None = None) -> list[Job]:
        """Jobs for a run, each with its steps and their timings.

        Step timings are the simulator's entire input. If a provider cannot supply them,
        it cannot support the waste audit -- only the observability surface.
        """
        ...

    async def fetch_logs(self, repo: Repo, job_id: int) -> bytes | None:
        """Raw log bytes for a job, or None if expired.

        Logs are retained 90 days by default. Callers must content-address the result and
        never re-fetch: log download is the rate-limit hog by a wide margin.
        """
        ...

    async def aclose(self) -> None: ...
