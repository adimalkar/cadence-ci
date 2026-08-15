"""Ingest orchestration.

Every day of delay permanently costs a day of history: GitHub retains logs for 90 days
and run metadata does not go back forever either. This is the only irreversible deadline
in the plan, which is why ingest ships before any detector.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import psycopg
import structlog

from cadence.models import Job, Repo, Run
from cadence.providers.base import CIProvider, RateLimited

log = structlog.get_logger(__name__)


@dataclass
class IngestStats:
    runs_seen: int = 0
    runs_written: int = 0
    jobs_written: int = 0
    steps_written: int = 0
    pages_fetched: int = 0
    not_modified: bool = False

    def __str__(self) -> str:
        return (
            f"{self.runs_written} runs, {self.jobs_written} jobs, "
            f"{self.steps_written} steps ({self.pages_fetched} pages)"
        )


def upsert_repo(conn: psycopg.Connection, repo: Repo) -> None:
    conn.execute(
        """
        INSERT INTO repo (id, owner, name, is_private, default_branch)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            owner = EXCLUDED.owner,
            name = EXCLUDED.name,
            is_private = EXCLUDED.is_private,
            default_branch = EXCLUDED.default_branch
        """,
        (repo.id, repo.owner, repo.name, repo.is_private, repo.default_branch),
    )


def upsert_run(conn: psycopg.Connection, run: Run) -> None:
    conn.execute(
        """
        INSERT INTO run (
            id, repo_id, workflow_id, workflow_path, workflow_name, run_number,
            run_attempt, event, status, conclusion, head_sha, head_branch, tree_sha,
            pull_request_number, created_at, started_at, updated_at
        ) VALUES (
            %(id)s, %(repo_id)s, %(workflow_id)s, %(workflow_path)s, %(workflow_name)s,
            %(run_number)s, %(run_attempt)s, %(event)s, %(status)s, %(conclusion)s,
            %(head_sha)s, %(head_branch)s, %(tree_sha)s, %(pull_request_number)s,
            %(created_at)s, %(started_at)s, %(updated_at)s
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            conclusion = EXCLUDED.conclusion,
            updated_at = EXCLUDED.updated_at,
            run_attempt = GREATEST(run.run_attempt, EXCLUDED.run_attempt)
        """,
        {
            "id": run.id,
            "repo_id": run.repo_id,
            "workflow_id": run.workflow_id,
            "workflow_path": run.workflow_path,
            "workflow_name": run.workflow_name,
            "run_number": run.run_number,
            "run_attempt": run.run_attempt,
            "event": run.event,
            "status": run.status,
            "conclusion": run.conclusion,
            "head_sha": run.head_sha,
            "head_branch": run.head_branch,
            "tree_sha": run.tree_sha,
            "pull_request_number": run.pull_request_number,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "updated_at": run.updated_at,
        },
    )


def ensure_run_stub(conn: psycopg.Connection, run: Run) -> None:
    """Satisfy job.run_id's foreign key when a workflow_job event arrives with no
    workflow_run row recorded yet.

    `ON CONFLICT DO NOTHING` is the entire safety property: this must never win against
    a fuller row. A workflow_job payload can never be authoritative about the run as a
    whole (its status/conclusion describe one job, not the run), so unlike `upsert_run`
    this never updates an existing row -- only a real workflow_run event or a REST
    backfill is allowed to do that.
    """
    conn.execute(
        """
        INSERT INTO run (id, repo_id, workflow_name, run_attempt, head_sha, head_branch,
                         created_at, started_at)
        VALUES (%(id)s, %(repo_id)s, %(workflow_name)s, %(run_attempt)s, %(head_sha)s,
                %(head_branch)s, %(created_at)s, %(started_at)s)
        ON CONFLICT (id) DO NOTHING
        """,
        {
            "id": run.id,
            "repo_id": run.repo_id,
            "workflow_name": run.workflow_name,
            "run_attempt": run.run_attempt,
            "head_sha": run.head_sha,
            "head_branch": run.head_branch,
            "created_at": run.created_at,
            "started_at": run.started_at,
        },
    )


def upsert_job(conn: psycopg.Connection, job: Job, repo_id: int) -> int:
    conn.execute(
        """
        INSERT INTO job (
            id, run_id, repo_id, name, status, conclusion, runner_labels, runner_group,
            created_at, started_at, completed_at, attempt, matrix
        ) VALUES (
            %(id)s, %(run_id)s, %(repo_id)s, %(name)s, %(status)s, %(conclusion)s,
            %(runner_labels)s, %(runner_group)s, %(created_at)s, %(started_at)s,
            %(completed_at)s, %(attempt)s, %(matrix)s
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            conclusion = EXCLUDED.conclusion,
            completed_at = EXCLUDED.completed_at
        """,
        {
            "id": job.id,
            "run_id": job.run_id,
            "repo_id": repo_id,
            "name": job.name,
            "status": job.status,
            "conclusion": job.conclusion,
            "runner_labels": job.runner_labels,
            "runner_group": job.runner_group,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "attempt": job.attempt,
            "matrix": psycopg.types.json.Json(job.matrix) if job.matrix else None,
        },
    )

    written = 0
    for step in job.steps:
        conn.execute(
            """
            INSERT INTO step (job_id, number, name, status, conclusion,
                              started_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id, number) DO UPDATE SET
                status = EXCLUDED.status,
                conclusion = EXCLUDED.conclusion,
                completed_at = EXCLUDED.completed_at
            """,
            (
                job.id,
                step.number,
                step.name,
                step.status,
                step.conclusion,
                step.started_at,
                step.completed_at,
            ),
        )
        written += 1
    return written


async def ingest_repo(
    provider: CIProvider,
    conn: psycopg.Connection,
    owner: str,
    name: str,
    *,
    limit: int = 100,
    fetch_jobs: bool = True,
    job_concurrency: int = 5,
) -> IngestStats:
    """Backfill a repo's recent run history, with per-step timings.

    `job_concurrency` is deliberately low. The jobs endpoint is one request per run, so a
    500-run backfill is 500 requests against a 5,000/hr budget -- fast enough, and leaving
    headroom matters more than finishing a single repo quickly when 50 are queued.
    """
    stats = IngestStats()

    repo = await provider.get_repo(owner, name)
    upsert_repo(conn, repo)
    conn.commit()

    row = conn.execute("SELECT runs_etag FROM repo WHERE id = %s", (repo.id,)).fetchone()
    etag = row["runs_etag"] if row else None

    collected: list[Run] = []
    page = 1
    while len(collected) < limit:
        # Only the first page carries the ETag: it is the one that changes when new runs
        # appear, and a 304 there means the whole delta poll can stop immediately.
        page_etag = etag if page == 1 else None
        result = await provider.fetch_runs(repo, page=page, per_page=100, etag=page_etag)
        stats.pages_fetched += 1

        if result.not_modified:
            log.info("ingest.not_modified", repo=repo.full_name)
            stats.not_modified = True
            return stats

        if page == 1 and result.etag:
            conn.execute(
                "UPDATE repo SET runs_etag = %s, last_polled_at = now() WHERE id = %s",
                (result.etag, repo.id),
            )

        if not result.runs:
            break
        collected.extend(result.runs)
        page += 1

    collected = collected[:limit]
    stats.runs_seen = len(collected)

    for run in collected:
        upsert_run(conn, run)
        stats.runs_written += 1
    conn.commit()

    if not fetch_jobs:
        return stats

    semaphore = asyncio.Semaphore(job_concurrency)

    async def _jobs_for(run: Run) -> tuple[Run, list[Job]]:
        async with semaphore:
            try:
                return run, await provider.fetch_jobs(repo, run.id)
            except RateLimited:
                raise
            except Exception as exc:  # one bad run must not abort a 500-run backfill
                log.warning("ingest.jobs_failed", run_id=run.id, error=str(exc))
                return run, []

    for coro in asyncio.as_completed([_jobs_for(r) for r in collected]):
        run, jobs = await coro
        for job in jobs:
            stats.steps_written += upsert_job(conn, job, repo.id)
            stats.jobs_written += 1
        run.jobs = jobs
    conn.commit()

    log.info("ingest.done", repo=repo.full_name, stats=str(stats))
    return stats
