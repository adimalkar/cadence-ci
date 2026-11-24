"""Bounded explicit repair of observed job/step data; not a coverage certificate."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

import httpx
import psycopg
from psycopg.rows import dict_row

from cadence.ingest import upsert_job
from cadence.models import Job, Repo
from cadence.providers.base import AccessDenied, Expired, NotFound, RateLimited

MAX_ID = 2**63 - 1


@dataclass(frozen=True)
class AttemptTarget:
    run_id: int
    repo_id: int
    attempt: int
    run_attempt: int
    head_sha: str
    updated_at: datetime | None


def validate_selection(ids: list[int], attempt: int | None, max_pages: int) -> None:
    if not ids or len(set(ids)) > 100 or any(
        type(n) is not int or not 1 <= n <= MAX_ID for n in ids
    ):
        raise ValueError("select 1..100 distinct positive stored run IDs")
    if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 2**31 - 1):
        raise ValueError("attempt must be positive")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max-pages must be between 1 and 100")


def select_targets(
    conn: psycopg.Connection, repo_id: int, ids: list[int], *, attempt: int | None = None,
) -> list[AttemptTarget]:
    validate_selection(ids, attempt, 10)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id,run_attempt,head_sha,updated_at,status FROM run"
                    " WHERE repo_id=%s AND id=ANY(%s)", (repo_id, ids))
        rows = {row["id"]: row for row in cur.fetchall()}
    if set(ids) != rows.keys():
        raise ValueError("all run IDs must already be stored for this repository")
    targets = []
    for run_id in dict.fromkeys(ids):
        row = rows[run_id]
        if row["status"] != "completed" or (attempt is not None and attempt > row["run_attempt"]):
            raise ValueError("selected runs must be completed and include the requested attempt")
        targets.append(AttemptTarget(run_id, repo_id, attempt or row["run_attempt"],
                                     row["run_attempt"], row["head_sha"], row["updated_at"]))
    return targets


def _timestamps(values: list[datetime | None]) -> None:
    present = [value for value in values if value is not None]
    if any(not isinstance(value, datetime) or value.utcoffset() is None for value in present):
        raise ValueError("job/step timestamps must include a timezone")
    if present != sorted(present):
        raise ValueError("job/step timestamps are out of order")


def validate_jobs(target: AttemptTarget, jobs: list[Job], max_pages: int) -> None:
    if len(jobs) > max_pages * 100:
        raise ValueError("job list exceeds the request budget")
    seen = set()
    for job in jobs:
        if (type(job.id) is not int or not 1 <= job.id <= MAX_ID or job.id in seen
                or job.run_id != target.run_id or job.attempt != target.attempt):
            raise ValueError("job identity is duplicated or belongs to a different attempt")
        seen.add(job.id)
        if job.status != "completed" or not job.conclusion or not job.name:
            raise ValueError("repair requires terminal named jobs with conclusions")
        _timestamps([job.created_at, job.started_at, job.completed_at])
        numbers = set()
        for step in job.steps:
            if type(step.number) is not int or step.number < 1 or step.number in numbers:
                raise ValueError("step numbers must be positive and unique within a job")
            numbers.add(step.number)
            if step.status != "completed" or not step.conclusion:
                raise ValueError("repair requires terminal steps with conclusions")
            _timestamps([step.started_at, step.completed_at])


def persist_jobs(conn: psycopg.Connection, target: AttemptTarget, jobs: list[Job]) -> dict:
    """Caller commits/rolls back one attempt, after HTTP. Preserve known omitted timings."""
    validate_jobs(target, jobs, 100)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT repo_id,run_attempt,head_sha,updated_at,status FROM run"
                    " WHERE id=%s FOR UPDATE", (target.run_id,))
        row = cur.fetchone()
        if row is None:
            return {"status": "missing"}
        if (row["repo_id"], row["run_attempt"], row["head_sha"], row["updated_at"],
            row["status"]) != (target.repo_id, target.run_attempt, target.head_sha,
                              target.updated_at, "completed"):
            return {"status": "run_changed"}
        steps_written = 0
        for job in sorted(jobs, key=lambda item: item.id):
            cur.execute("SELECT * FROM job WHERE id=%s FOR UPDATE", (job.id,))
            old = cur.fetchone()
            if old is not None and (old["run_id"], old["repo_id"], old["attempt"]) != (
                target.run_id, target.repo_id, target.attempt
            ):
                raise ValueError("stored job identity conflicts with the requested attempt")
            cur.execute("SELECT * FROM step WHERE job_id=%s", (job.id,))
            old_steps = {step["number"]: step for step in cur.fetchall()}
            merged_steps = []
            for step in job.steps:
                previous = old_steps.get(step.number, {})
                merged_steps.append(replace(
                    step, started_at=step.started_at or previous.get("started_at"),
                    completed_at=step.completed_at or previous.get("completed_at"),
                ))
            previous_job = old or {}
            merged = replace(
                job, created_at=job.created_at or previous_job.get("created_at"),
                started_at=job.started_at or previous_job.get("started_at"),
                completed_at=job.completed_at or previous_job.get("completed_at"),
                steps=merged_steps,
            )
            validate_jobs(target, [merged], 100)
            steps_written += upsert_job(conn, merged, target.repo_id)
            cur.execute("UPDATE job SET created_at=%s,name=%s,name_base=%s WHERE id=%s",
                        (merged.created_at, merged.name, merged.name_base, merged.id))
            for step in merged.steps:
                cur.execute("UPDATE step SET started_at=%s WHERE job_id=%s AND number=%s",
                            (step.started_at, job.id, step.number))
    return {"status": "refreshed" if jobs else "empty", "jobs_written": len(jobs),
            "steps_written": steps_written}


class RepairProvider(Protocol):
    async def fetch_jobs(
        self, repo: Repo, run_id: int, *, attempt: int, max_pages: int,
    ) -> list[Job]: ...


async def repair_jobs(
    provider: RepairProvider, repo: Repo, targets: list[AttemptTarget],
    persist: Callable[[AttemptTarget, list[Job]], dict], *, max_pages: int = 10,
) -> dict:
    validate_selection([t.run_id for t in targets], None, max_pages)
    outcomes, stopped = [], None
    for target in targets:
        if target.repo_id != repo.id:
            raise ValueError("target belongs to a different repository")
        item = {"run_id": target.run_id, "attempt": target.attempt}
        try:
            jobs = await provider.fetch_jobs(repo, target.run_id, attempt=target.attempt,
                                             max_pages=max_pages)
            validate_jobs(target, jobs, max_pages)
        except (NotFound, Expired):
            outcomes.append({**item, "status": "unavailable"})
            continue
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            outcomes.append({**item, "status": "invalid_metadata", "detail": str(exc)})
            continue
        except (RateLimited, AccessDenied, httpx.HTTPError) as exc:
            reason = ("rate_limited" if isinstance(exc, RateLimited) else
                      "access_denied" if isinstance(exc, AccessDenied) else "transport_error")
            stopped = {**item, "reason": reason}
            if isinstance(exc, RateLimited):
                stopped["retry_after_seconds"] = exc.retry_after_seconds
            break
        try:
            result = persist(target, jobs)
        except (psycopg.Error, ValueError) as exc:
            stopped = {**item, "reason": "persistence_error", "detail": type(exc).__name__}
            break
        outcomes.append({**item, **result})
    return {"selected_attempts": [{"run_id": t.run_id, "attempt": t.attempt} for t in targets],
            "outcomes": outcomes, "counts": dict(Counter(row["status"] for row in outcomes)),
            "remaining_attempts": [{"run_id": t.run_id, "attempt": t.attempt}
                                   for t in targets[len(outcomes):]], "stopped": stopped,
            "coverage": "unverified; no inventory receipt is created",
            "max_pages_per_attempt": max_pages}
