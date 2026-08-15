"""The ingest job queue: Postgres, `FOR UPDATE SKIP LOCKED`, no broker.

Every function opens its own dict-row cursor rather than trusting the caller's
connection to be configured that way -- the queue must behave the same whether it's
called from the app's `connect()` (which defaults to dict rows) or a bare
`psycopg.connect()` in a test.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json


def enqueue(
    conn: psycopg.Connection, kind: str, payload: dict, *, run_at: datetime | None = None
) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO ingest_job (kind, payload, run_at)"
            " VALUES (%s, %s, coalesce(%s, now())) RETURNING id",
            (kind, Json(payload), run_at),
        )
        return cur.fetchone()["id"]


# A claim is a lease, not a lock. A worker killed mid-job (deploy, OOM, SIGKILL) leaves
# its row in 'processing' with nobody to finish it; without expiry that row is stranded
# forever and `--until-empty` never terminates, because it counts as in-flight work.
LEASE = timedelta(minutes=15)


def claim_next(conn: psycopg.Connection, *, lease: timedelta = LEASE) -> dict | None:
    """Atomically claim one runnable job, or reclaim one whose lease expired.

    SKIP LOCKED lets any number of workers poll this table concurrently without
    blocking each other on a row another worker already holds -- the reason a job
    queue can live in Postgres instead of a separate broker.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE ingest_job SET status = 'processing', updated_at = now()
            WHERE id = (
              SELECT id FROM ingest_job
              WHERE (status = 'pending' AND run_at <= now())
                 OR (status = 'processing' AND updated_at < now() - %s)
              ORDER BY run_at
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            RETURNING *
            """,
            (lease,),
        )
        return cur.fetchone()


def complete(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE ingest_job SET status = 'done', updated_at = now() WHERE id = %s", (job_id,)
    )


def fail(conn: psycopg.Connection, job_id: int, error: str, *, retry_in: timedelta | None) -> None:
    """`retry_in=None` is terminal. A rate limit or a transient network error should
    retry; a job whose payload is simply wrong (a deleted repo, a malformed webhook
    body) should not retry forever."""
    if retry_in is not None:
        conn.execute(
            "UPDATE ingest_job SET status = 'pending', attempts = attempts + 1,"
            " last_error = %s, run_at = now() + %s, updated_at = now() WHERE id = %s",
            (error[:2000], retry_in, job_id),
        )
    else:
        conn.execute(
            "UPDATE ingest_job SET status = 'failed', attempts = attempts + 1,"
            " last_error = %s, updated_at = now() WHERE id = %s",
            (error[:2000], job_id),
        )


def pending_or_processing_count(conn: psycopg.Connection, *, lease: timedelta = LEASE) -> int:
    """Claimable-right-now work, deliberately excluding future-scheduled rows.

    A corpus repo's `poll_repo` job re-enqueues itself 30 minutes out on every
    completion, so a naive "any pending row" count would never reach zero. This is
    what lets `worker run --until-empty` terminate after draining the current backlog
    while leaving the recurring continuations in place for a long-running worker to
    pick up later.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT count(*) AS n FROM ingest_job"
            " WHERE (status = 'pending' AND run_at <= now())"
            # Only count leases still live: an expired one is reclaimable work, which
            # claim_next will hand out, but a permanently-stranded row must not keep
            # --until-empty spinning forever.
            "    OR (status = 'processing' AND updated_at >= now() - %s)",
            (lease,),
        )
        return cur.fetchone()["n"]
