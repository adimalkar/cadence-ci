"""Ingest orchestration.

Every day of delay permanently costs a day of history: GitHub retains logs for 90 days
and run metadata does not go back forever either. This is the only irreversible deadline
in the plan, which is why ingest ships before any detector.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import structlog
from psycopg.rows import dict_row

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
            run_attempt = GREATEST(run.run_attempt, EXCLUDED.run_attempt),
            -- A run first seen as `queued` has no run_started_at yet. Without this the
            -- row keeps started_at NULL forever, because every later update omitted it.
            -- coalesce in this direction so a real timestamp is never overwritten by a
            -- NULL from a payload that simply didn't carry one.
            started_at = coalesce(EXCLUDED.started_at, run.started_at),
            tree_sha = coalesce(EXCLUDED.tree_sha, run.tree_sha),
            pull_request_number =
                coalesce(EXCLUDED.pull_request_number, run.pull_request_number)
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


def _store_etag(conn: psycopg.Connection, repo_id: int, etag: str | None) -> None:
    """Advance the delta-poll cursor. Call only after the window's jobs and steps are
    durably written -- an ETag stored early silently skips them on the next poll."""
    conn.execute(
        "UPDATE repo SET runs_etag = coalesce(%s, runs_etag), last_polled_at = now()"
        " WHERE id = %s",
        (etag, repo_id),
    )
    conn.commit()


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
            id, run_id, repo_id, name, name_base, status, conclusion, runner_labels,
            runner_group, created_at, started_at, completed_at, attempt, matrix
        ) VALUES (
            %(id)s, %(run_id)s, %(repo_id)s, %(name)s, %(name_base)s, %(status)s,
            %(conclusion)s, %(runner_labels)s, %(runner_group)s, %(created_at)s,
            %(started_at)s, %(completed_at)s, %(attempt)s, %(matrix)s
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            conclusion = EXCLUDED.conclusion,
            completed_at = EXCLUDED.completed_at,
            -- Webhooks deliver a job three times (queued -> in_progress -> completed),
            -- and only the last two carry started_at. Omitting it here left every
            -- webhook-ingested job with started_at NULL permanently, which silently
            -- voids execution_time, queue_time, and every downstream billing figure.
            started_at = coalesce(EXCLUDED.started_at, job.started_at),
            runner_group = coalesce(EXCLUDED.runner_group, job.runner_group),
            -- labels arrive empty on some queued payloads; don't let that erase them
            runner_labels = CASE
                WHEN EXCLUDED.runner_labels = '{}' THEN job.runner_labels
                ELSE EXCLUDED.runner_labels END,
            matrix = coalesce(EXCLUDED.matrix, job.matrix)
        """,
        {
            "id": job.id,
            "run_id": job.run_id,
            "repo_id": repo_id,
            "name": job.name,
            "name_base": job.name_base,
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

    # Explicit row factory: ingest_repo accepts a caller's connection, so it must not
    # depend on how that caller happened to configure it.
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT runs_etag FROM repo WHERE id = %s", (repo.id,))
        row = cur.fetchone()
    etag = row["runs_etag"] if row else None

    collected: list[Run] = []
    new_etag: str | None = None
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

        # NOTE: the ETag is deliberately NOT persisted here. Storing it before the jobs
        # below succeed means a failure mid-fetch still advances the cursor, and the next
        # poll 304s and returns early -- permanently losing the step timings for these
        # runs, with no way to notice and no way to backfill past 90-day retention.
        if page == 1:
            new_etag = result.etag

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
        _store_etag(conn, repo.id, new_etag)
        return stats

    await _store_jobs(provider, conn, repo, collected, stats, job_concurrency)
    conn.commit()

    # Only now is the cursor safe to advance: every run in this window has its jobs and
    # steps durably written.
    _store_etag(conn, repo.id, new_etag)

    log.info("ingest.done", repo=repo.full_name, stats=str(stats))
    return stats


async def _store_jobs(
    provider: CIProvider,
    conn: psycopg.Connection,
    repo: Repo,
    runs: list[Run],
    stats: IngestStats,
    job_concurrency: int,
) -> None:
    """Fetch and upsert every job (and earlier attempts) of `runs`. Caller commits."""
    semaphore = asyncio.Semaphore(job_concurrency)

    async def _jobs_for(run: Run) -> tuple[Run, list[Job]]:
        async with semaphore:
            try:
                jobs = await provider.fetch_jobs(repo, run.id)
                # A re-run's earlier attempts are the single strongest flaky label
                # available ("retry succeeded, no intervening commit"), and
                # filter=latest hides them -- so a rerun's failing jobs were being
                # dropped entirely. Only reruns pay the extra calls (~0.6% of runs).
                for attempt in range(1, run.run_attempt):
                    jobs.extend(await provider.fetch_jobs(repo, run.id, attempt=attempt))
                return run, jobs
            except RateLimited:
                raise
            except Exception as exc:  # one bad run must not abort a 500-run backfill
                log.warning("ingest.jobs_failed", run_id=run.id, error=str(exc))
                return run, []

    tasks = [asyncio.create_task(_jobs_for(r)) for r in runs]
    try:
        for coro in asyncio.as_completed(tasks):
            run, jobs = await coro
            for job in jobs:
                stats.steps_written += upsert_job(conn, job, repo.id)
                stats.jobs_written += 1
            run.jobs = jobs
    except BaseException:
        # Without this, a raise here leaves the remaining tasks running against a client
        # the caller is about to close, surfacing as unretrieved exceptions on a
        # background task rather than as this error.
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# GitHub returns at most this many runs for any filtered listing, however many pages.
FILTERED_LISTING_CAP = 1000


@dataclass
class BackfillStats(IngestStats):
    runs_skipped_known: int = 0
    windows_split: int = 0

    def __str__(self) -> str:
        return (
            super().__str__()
            + f", {self.runs_skipped_known} already stored, {self.windows_split} splits"
        )


def _created_filter(start: datetime, end: datetime) -> str:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return f"{start.astimezone(UTC).strftime(fmt)}..{end.astimezone(UTC).strftime(fmt)}"


async def backfill_window(
    provider: CIProvider,
    conn: psycopg.Connection,
    repo: Repo,
    start: datetime,
    end: datetime,
    *,
    fetch_jobs: bool = True,
    job_concurrency: int = 5,
    stats: BackfillStats | None = None,
) -> BackfillStats:
    """Store every run created in [start, end], with its jobs, then commit.

    The history a delta poll cannot reach: `ingest_repo` walks newest-first and stops at
    its limit. Three differences from it, all deliberate:

    - The ETag cursor is never read or written. It belongs to the delta poll, and a
      backfill of August must not make September's poll 304.
    - Runs whose jobs are already stored are not re-fetched, so an interrupted backfill
      resumes at the cost of its listing calls only.
    - A window that fills GitHub's 1,000-run cap for filtered listings is split in half
      and both halves fetched, rather than silently truncated.

    Commits once, at the end: a window is all-or-nothing, so a failure leaves nothing
    half-written for a resume to mistake as complete.
    """
    stats = stats if stats is not None else BackfillStats()
    upsert_repo(conn, repo)

    collected: dict[int, Run] = {}
    page = 1
    per_page = 100
    while True:
        result = await provider.fetch_runs(
            repo, page=page, per_page=per_page, created=_created_filter(start, end)
        )
        stats.pages_fetched += 1
        for run in result.runs:
            collected[run.id] = run
        if len(result.runs) < per_page:
            break
        if page * per_page >= FILTERED_LISTING_CAP:
            if end - start <= timedelta(minutes=1):
                raise RuntimeError(
                    f"over {FILTERED_LISTING_CAP} runs created within one minute "
                    f"({_created_filter(start, end)}); cannot split further"
                )
            # Possibly truncated: discard this pass and fetch each half on its own.
            stats.windows_split += 1
            mid = start + (end - start) / 2
            await backfill_window(provider, conn, repo, start, mid, fetch_jobs=fetch_jobs,
                                  job_concurrency=job_concurrency, stats=stats)
            await backfill_window(provider, conn, repo, mid + timedelta(seconds=1), end,
                                  fetch_jobs=fetch_jobs, job_concurrency=job_concurrency,
                                  stats=stats)
            return stats
        page += 1

    runs = list(collected.values())
    stats.runs_seen += len(runs)
    known: set[int] = set()
    if runs and fetch_jobs:
        with conn.cursor(row_factory=dict_row) as cur:
            # Completed when stored, and with jobs: nothing left to learn from GitHub.
            cur.execute(
                """
                SELECT r.id FROM run r
                WHERE r.id = ANY(%s) AND r.status = 'completed'
                  AND EXISTS (SELECT 1 FROM job j WHERE j.run_id = r.id)
                """,
                ([r.id for r in runs],),
            )
            known = {row["id"] for row in cur.fetchall()}
    for run in runs:
        upsert_run(conn, run)
        stats.runs_written += 1
    if fetch_jobs:
        todo = [r for r in runs if r.id not in known]
        stats.runs_skipped_known += len(runs) - len(todo)
        await _store_jobs(provider, conn, repo, todo, stats, job_concurrency)
    conn.commit()
    return stats
