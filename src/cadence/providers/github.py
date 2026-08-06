"""GitHub Actions provider.

Read-only. Needs no App install -- a personal token with `public_repo` is enough for the
whole corpus, which is what makes pre-install analysis possible.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import httpx
import structlog

from cadence.models import Job, Repo, Run, RunPage, Step
from cadence.providers.base import NotFound, RateLimited

log = structlog.get_logger(__name__)

API_ROOT = "https://api.github.com"

# Matrix legs arrive only inside the job name, as "build (ubuntu-latest, 20)". The jobs
# API does not expose the matrix dict. Parsing it out is what makes the
# non-discriminating-leg rule possible.
_MATRIX_IN_NAME = re.compile(r"^(?P<base>.+?)\s*\((?P<args>[^()]*)\)\s*$")


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_matrix(job_name: str) -> tuple[str, dict | None]:
    """Split "test (ubuntu-latest, 3.12)" into ("test", {"0": ..., "1": ...}).

    Positional keys because GitHub discards the matrix key names by the time the job is
    named. Correlating positions to dimension names requires the workflow YAML, which
    Phase 1 has and Phase 0 does not -- so store positions now and enrich later rather
    than guessing.
    """
    match = _MATRIX_IN_NAME.match(job_name)
    if not match:
        return job_name, None
    args = [a.strip() for a in match.group("args").split(",") if a.strip()]
    if not args:
        return job_name, None
    return match.group("base").strip(), {str(i): v for i, v in enumerate(args)}


class GitHubProvider:
    name = "github_actions"

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=API_ROOT,
            timeout=httpx.Timeout(30.0, read=120.0),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cadence/0.1",
                "Authorization": f"Bearer {token}",
            },
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ transport

    async def _get(
        self, path: str, *, params: dict | None = None, etag: str | None = None
    ) -> httpx.Response:
        headers = {"If-None-Match": etag} if etag else None
        resp = await self._client.get(path, params=params, headers=headers)

        if resp.status_code in (403, 429):
            raise RateLimited(self._retry_after(resp), resp.text[:200])
        if resp.status_code == 404:
            raise NotFound(path)
        if resp.status_code not in (200, 304):
            resp.raise_for_status()
        return resp

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        """How long to wait, per GitHub's own signalling.

        Three mechanisms, in the order GitHub documents them: an explicit `Retry-After`
        (secondary limits), a `x-ratelimit-reset` epoch (primary limits), and a fallback.
        Guessing instead of reading these is how ingest gets an account throttled.
        """
        if (retry_after := resp.headers.get("retry-after")) is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        if resp.headers.get("x-ratelimit-remaining") == "0":
            reset = resp.headers.get("x-ratelimit-reset")
            if reset is not None:
                try:
                    delta = float(reset) - datetime.now(UTC).timestamp()
                    return max(delta, 1.0)
                except ValueError:
                    pass
        return 60.0

    async def _get_with_backoff(self, path: str, *, params: dict | None = None, tries: int = 3):
        """Retry transport errors only.

        Rate limits deliberately propagate: a limit means the whole ingest worker should
        pause and reschedule, not that this one request should sleep inside a coroutine
        holding a database connection.
        """
        for attempt in range(tries):
            try:
                return await self._get(path, params=params)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == tries - 1:
                    raise
                wait = 2.0**attempt
                log.warning("github.retry", path=path, attempt=attempt, wait=wait, error=str(exc))
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ api surface

    async def get_repo(self, owner: str, name: str) -> Repo:
        resp = await self._get_with_backoff(f"/repos/{owner}/{name}")
        data = resp.json()
        return Repo(
            id=data["id"],
            owner=data["owner"]["login"],
            name=data["name"],
            is_private=data["private"],
            default_branch=data.get("default_branch"),
        )

    async def fetch_runs(
        self,
        repo: Repo,
        *,
        page: int = 1,
        per_page: int = 100,
        etag: str | None = None,
    ) -> RunPage:
        resp = await self._get(
            f"/repos/{repo.owner}/{repo.name}/actions/runs",
            params={"page": page, "per_page": per_page},
            etag=etag,
        )
        if resp.status_code == 304:
            return RunPage(runs=[], etag=etag, not_modified=True)

        payload = resp.json()
        runs = [self._to_run(raw, repo.id) for raw in payload.get("workflow_runs", [])]
        return RunPage(runs=runs, etag=resp.headers.get("etag"))

    @staticmethod
    def _to_run(raw: dict, repo_id: int) -> Run:
        prs = raw.get("pull_requests") or []
        return Run(
            id=raw["id"],
            repo_id=repo_id,
            workflow_id=raw.get("workflow_id"),
            workflow_path=raw.get("path"),
            workflow_name=raw.get("name"),
            run_number=raw.get("run_number"),
            run_attempt=raw.get("run_attempt", 1),
            event=raw.get("event"),
            status=raw.get("status"),
            conclusion=raw.get("conclusion"),
            head_sha=raw["head_sha"],
            head_branch=raw.get("head_branch"),
            # The run payload carries the *commit* sha only. tree_sha -- which enables the
            # strongest flaky label (same tree, different outcome) -- costs an extra commit
            # lookup and is backfilled separately rather than paid for on every run.
            tree_sha=None,
            pull_request_number=prs[0]["number"] if prs else None,
            created_at=_dt(raw["created_at"]),
            started_at=_dt(raw.get("run_started_at")),
            updated_at=_dt(raw.get("updated_at")),
        )

    async def fetch_jobs(self, repo: Repo, run_id: int, *, attempt: int | None = None) -> list[Job]:
        base = f"/repos/{repo.owner}/{repo.name}/actions/runs/{run_id}"
        path = f"{base}/attempts/{attempt}/jobs" if attempt else f"{base}/jobs"

        jobs: list[Job] = []
        page = 1
        while True:
            resp = await self._get_with_backoff(
                path, params={"page": page, "per_page": 100, "filter": "latest"}
            )
            payload = resp.json()
            batch = payload.get("jobs", [])
            jobs.extend(self._to_job(raw, run_id) for raw in batch)

            total = payload.get("total_count", len(jobs))
            if len(jobs) >= total or not batch:
                break
            page += 1
        return jobs

    @staticmethod
    def _to_job(raw: dict, run_id: int) -> Job:
        name, matrix = _parse_matrix(raw["name"])
        steps = [
            Step(
                number=s.get("number", i),
                name=s.get("name", ""),
                status=s.get("status"),
                conclusion=s.get("conclusion"),
                started_at=_dt(s.get("started_at")),
                completed_at=_dt(s.get("completed_at")),
            )
            for i, s in enumerate(raw.get("steps") or [])
        ]
        return Job(
            id=raw["id"],
            run_id=run_id,
            name=name,
            status=raw.get("status"),
            conclusion=raw.get("conclusion"),
            runner_labels=raw.get("labels") or [],
            runner_group=raw.get("runner_group_name"),
            created_at=_dt(raw.get("created_at")),
            started_at=_dt(raw.get("started_at")),
            completed_at=_dt(raw.get("completed_at")),
            attempt=raw.get("run_attempt", 1),
            steps=steps,
            matrix=matrix,
        )

    async def fetch_logs(self, repo: Repo, job_id: int) -> bytes | None:
        """Job logs, or None once GitHub has expired them (410) or never had them (404)."""
        try:
            resp = await self._get(f"/repos/{repo.owner}/{repo.name}/actions/jobs/{job_id}/logs")
        except NotFound:
            return None
        if resp.status_code == 410:
            return None
        return resp.content
