"""Attempt-specific REST inventory evidence; completeness is checked against live rows."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from cadence.models import Job

MAX_JOBS = 10_000
MAX_ID = 2**63 - 1


@dataclass(frozen=True)
class JobInventory:
    repo_id: int
    run_id: int
    attempt: int
    expected_count: int
    pages: int
    observed_at: datetime
    jobs: tuple[Job, ...]

    def validate(self) -> None:
        if any(type(n) is not int or not 1 <= n <= MAX_ID for n in (self.repo_id, self.run_id)):
            raise ValueError("invalid inventory identity")
        if type(self.attempt) is not int or not 1 <= self.attempt <= 2**31 - 1:
            raise ValueError("invalid inventory attempt")
        if (type(self.expected_count) is not int or not 0 <= self.expected_count <= MAX_JOBS
                or len(self.jobs) != self.expected_count):
            raise ValueError("inventory count does not match its bounded job list")
        if type(self.pages) is not int or not 1 <= self.pages <= MAX_JOBS:
            raise ValueError("invalid inventory page count")
        if not isinstance(self.observed_at, datetime) or self.observed_at.utcoffset() is None:
            raise ValueError("inventory observation needs a timezone")
        ids = []
        for job in self.jobs:
            if (type(job.id) is not int or not 1 <= job.id <= MAX_ID
                    or job.run_id != self.run_id or job.attempt != self.attempt):
                raise ValueError("inventory contains a different run/attempt or invalid job ID")
            ids.append(job.id)
        if len(set(ids)) != len(ids):
            raise ValueError("inventory contains duplicate job IDs")


def store_inventory(conn: psycopg.Connection, inventory: JobInventory) -> bool:
    """Caller writes jobs and receipt in one transaction. No implicit commit or API."""
    inventory.validate()
    if any(job.status != "completed" for job in inventory.jobs):
        return False
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT repo_id,run_attempt,status FROM run WHERE id=%s FOR UPDATE",
                    (inventory.run_id,))
        run = cur.fetchone()
        if (run is None or run["repo_id"] != inventory.repo_id
                or run["run_attempt"] < inventory.attempt):
            raise ValueError("inventory does not match the stored run")
        if run["run_attempt"] == inventory.attempt and run["status"] != "completed":
            return False
        cur.execute("SELECT id,repo_id,status FROM job WHERE run_id=%s AND attempt=%s",
                    (inventory.run_id, inventory.attempt))
        rows = cur.fetchall()
        ids = sorted(job.id for job in inventory.jobs)
        if (sorted(row["id"] for row in rows) != ids
                or any(row["repo_id"] != inventory.repo_id or row["status"] != "completed"
                       for row in rows)):
            raise ValueError("stored jobs do not match the terminal inventory")
        cur.execute(
            """INSERT INTO run_job_inventory
               (repo_id,run_id,attempt,expected_count,job_ids,pages,observed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,attempt) DO UPDATE SET
                 expected_count=EXCLUDED.expected_count,job_ids=EXCLUDED.job_ids,
                 pages=EXCLUDED.pages,observed_at=EXCLUDED.observed_at
               WHERE run_job_inventory.repo_id = EXCLUDED.repo_id AND
                 (run_job_inventory.observed_at < EXCLUDED.observed_at
                  OR (run_job_inventory.observed_at = EXCLUDED.observed_at
                      AND run_job_inventory.job_ids = EXCLUDED.job_ids))
               RETURNING run_id""",
            (inventory.repo_id, inventory.run_id, inventory.attempt, inventory.expected_count, ids,
             inventory.pages, inventory.observed_at))
        return cur.fetchone() is not None


def job_coverage(
    conn: psycopg.Connection, repo_id: int, *, run_ids: list[int] | None = None, limit: int = 50,
) -> dict:
    """Verify the CURRENT attempt's stored membership in a consistent caller snapshot.

    The receipt alone is historical evidence. Never treat a populated receipt table as a
    certificate: legacy workers/webhooks may have changed jobs since it was recorded.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if run_ids is not None and (not run_ids or len(set(run_ids)) > limit or any(
        type(n) is not int or not 1 <= n <= MAX_ID for n in run_ids
    )):
        raise ValueError("explicit run IDs must be positive and fit within --limit")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT r.id,r.run_attempt,r.status,i.repo_id AS inventory_repo_id,
                      i.expected_count,i.job_ids,i.pages,i.observed_at
               FROM run r LEFT JOIN run_job_inventory i
                 ON i.run_id=r.id AND i.attempt=r.run_attempt
               WHERE r.repo_id=%s AND (%s::bigint[] IS NULL OR r.id=ANY(%s))
               ORDER BY r.id DESC LIMIT %s""",
            (repo_id, run_ids, run_ids, limit + 1))
        rows = cur.fetchall()
        if run_ids is not None and set(run_ids) != {row["id"] for row in rows}:
            raise ValueError("one or more run IDs are not stored for this repository")
        truncated = len(rows) > limit
        rows = rows[:limit]
        ids = [row["id"] for row in rows]
        cur.execute(
            """SELECT id,run_id,repo_id,attempt,status FROM job WHERE run_id=ANY(%s)
               ORDER BY id LIMIT 50001""", (ids,))
        jobs = cur.fetchall()
        if len(jobs) > 50_000:
            raise ValueError("coverage exceeds 50000 stored jobs; select fewer runs")
    by_attempt: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for job in jobs:
        by_attempt[(job["run_id"], job["attempt"])].append(job)
    reports = []
    for row in rows:
        current = by_attempt[(row["id"], row["run_attempt"])]
        stored = {job["id"] for job in current}
        expected = set(row["job_ids"] or [])
        if row["expected_count"] is None:
            state = "unknown"
        elif row["inventory_repo_id"] != repo_id or any(
            job["repo_id"] != repo_id for job in current
        ):
            state = "identity_mismatch"
        elif expected != stored or len(expected) != row["expected_count"]:
            state = "inventory_mismatch"
        elif row["status"] != "completed":
            state = "run_incomplete"
        elif any(job["status"] != "completed" for job in current):
            state = "jobs_incomplete"
        else:
            state = "verified"
        reports.append({"run_id": row["id"], "attempt": row["run_attempt"], "coverage": state,
                        "expected_count": row["expected_count"], "stored_count": len(stored),
                        "pages": row["pages"],
                        "observed_at": row["observed_at"].isoformat()
                        if row["observed_at"] else None,
                        "missing_job_ids": sorted(expected - stored)
                        if row["expected_count"] is not None else None,
                        "extra_job_ids": sorted(stored - expected)
                        if row["expected_count"] is not None else None})
    return {"runs": reports, "counts": dict(Counter(row["coverage"] for row in reports)),
            "truncated": truncated,
            "meaning": "verified means terminal stored membership matches a REST attempt receipt; "
                       "not proof of timing accuracy, snapshot isolation or causal savings"}
