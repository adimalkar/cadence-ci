"""The queue consumer. Drains `poll_repo`, `webhook_event`, and `fetch_log` jobs.

Concurrency is N independent claim-loops racing on the same table, each with its own
connection -- exactly what `SKIP LOCKED` exists to make safe, and simpler than a shared
worker pool with its own coordination logic.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from cadence import queue
from cadence.db import connect
from cadence.ingest import ensure_run_stub, ingest_repo, upsert_job, upsert_repo, upsert_run
from cadence.logstore import LocalLogStore, store_job_log
from cadence.models import Repo
from cadence.providers.base import CIProvider, RateLimited

log = structlog.get_logger(__name__)

POLL_INTERVAL = timedelta(minutes=30)
MAX_ATTEMPTS = 5


async def _handle_poll_repo(provider: CIProvider, job: dict) -> None:
    payload = job["payload"]
    owner, name, limit = payload["owner"], payload["name"], payload.get("limit", 100)
    with connect() as conn:
        stats = await ingest_repo(provider, conn, owner, name, limit=limit)
        # The recurring tail: re-enqueue the same repo 30 minutes out. This one line is
        # what turns a single backfill into continuous ingest -- as long as a worker
        # keeps running, history keeps accumulating with no further intervention.
        queue.enqueue(conn, "poll_repo", payload, run_at=datetime.now(UTC) + POLL_INTERVAL)
        conn.commit()
    log.info("worker.poll_repo.done", repo=f"{owner}/{name}", stats=str(stats))


async def _handle_webhook_event(provider: CIProvider, job: dict) -> None:
    payload = job["payload"]
    event, delivery_id, body = payload["event"], payload["delivery_id"], payload["body"]
    normalized = provider.normalize_event(event, body)

    with connect() as conn:
        if normalized is not None:
            upsert_repo(conn, normalized.repo)
            if normalized.run is not None:
                if event == "workflow_run":
                    # Authoritative: this event describes the run as a whole.
                    upsert_run(conn, normalized.run)
                else:
                    # A workflow_job's run-level fields are a partial view (job status
                    # is not run status) -- never allowed to overwrite a fuller row.
                    ensure_run_stub(conn, normalized.run)
            for j in normalized.jobs:
                upsert_job(conn, j, normalized.repo.id)
        conn.execute(
            "UPDATE webhook_delivery SET processed_at = now() WHERE delivery_id = %s",
            (delivery_id,),
        )
        conn.commit()


async def _handle_fetch_log(provider: CIProvider, log_store: LocalLogStore, job: dict) -> None:
    payload = job["payload"]
    with connect() as conn:
        row = conn.execute(
            "SELECT id, owner, name, is_private, default_branch FROM repo WHERE id = %s",
            (payload["repo_id"],),
        ).fetchone()
        if row is None:
            return  # repo no longer tracked; nothing to do
        repo = Repo(
            id=row["id"],
            owner=row["owner"],
            name=row["name"],
            is_private=row["is_private"],
            default_branch=row["default_branch"],
        )
        await store_job_log(provider, conn, log_store, repo, payload["job_id"])
        conn.commit()


async def _dispatch(provider: CIProvider, log_store: LocalLogStore, job: dict) -> None:
    kind = job["kind"]
    if kind == "poll_repo":
        await _handle_poll_repo(provider, job)
    elif kind == "webhook_event":
        await _handle_webhook_event(provider, job)
    elif kind == "fetch_log":
        await _handle_fetch_log(provider, log_store, job)
    else:
        raise ValueError(f"unknown job kind: {kind}")


async def run_worker(
    provider: CIProvider,
    log_store: LocalLogStore,
    *,
    concurrency: int = 4,
    until_empty: bool = False,
    idle_sleep: float = 2.0,
    max_idle_iterations: int | None = None,
) -> None:
    async def _loop() -> None:
        idle = 0
        while True:
            with connect() as conn:
                job = queue.claim_next(conn)
                conn.commit()

            if job is None:
                if until_empty:
                    with connect() as conn:
                        remaining = queue.pending_or_processing_count(conn)
                    if remaining == 0:
                        return
                    await asyncio.sleep(idle_sleep)
                    continue
                idle += 1
                if max_idle_iterations is not None and idle >= max_idle_iterations:
                    return
                await asyncio.sleep(idle_sleep)
                continue

            idle = 0
            try:
                await _dispatch(provider, log_store, job)
            except RateLimited as exc:
                with connect() as conn:
                    queue.fail(
                        conn, job["id"], str(exc),
                        retry_in=timedelta(seconds=exc.retry_after_seconds),
                    )
                    conn.commit()
            except Exception as exc:  # one bad job must not kill the worker
                log.warning("worker.job_failed", kind=job["kind"], id=job["id"], error=str(exc))
                retry = timedelta(minutes=1) if job["attempts"] < MAX_ATTEMPTS else None
                with connect() as conn:
                    queue.fail(conn, job["id"], str(exc), retry_in=retry)
                    conn.commit()
            else:
                with connect() as conn:
                    queue.complete(conn, job["id"])
                    conn.commit()

    await asyncio.gather(*[_loop() for _ in range(concurrency)])
