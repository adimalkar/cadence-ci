"""Bounded exact-run metadata refresh; no polling cursor or job timing changes."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx
import psycopg
from psycopg.rows import dict_row

from cadence.ingest import upsert_run
from cadence.models import Repo, Run
from cadence.providers.base import AccessDenied, Expired, NotFound, RateLimited

MAX_RUN_ID = 2**63 - 1
MAX_REFRESH = 500


def valid_run_id(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_RUN_ID


def select_run_ids(
    conn: psycopg.Connection, repo_id: int, *, limit: int = 50,
    run_ids: list[int] | None = None, before: int | None = None,
) -> dict:
    if type(limit) is not int or not 1 <= limit <= MAX_REFRESH:
        raise ValueError("limit must be between 1 and 500")
    if before is not None and not valid_run_id(before):
        raise ValueError("before must be a positive run ID")
    if run_ids is not None:
        if before is not None:
            raise ValueError("choose explicit run IDs or a before cursor")
        ids = list(dict.fromkeys(run_ids))
        if not ids or len(ids) > limit or any(not valid_run_id(n) for n in ids):
            raise ValueError("explicit run IDs must be positive and fit within --limit")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM run WHERE repo_id = %s AND id = ANY(%s)", (repo_id, ids))
            found = {row["id"] for row in cur.fetchall()}
        missing = [n for n in ids if n not in found]
        if missing:
            raise ValueError(f"run IDs are not stored for this repository: {missing}")
        return {"run_ids": ids, "next_before": None}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM run WHERE repo_id = %s"
                    " AND (%s::bigint IS NULL OR id < %s)"
                    " ORDER BY id DESC LIMIT %s", (repo_id, before, before, limit + 1))
        ids = [row["id"] for row in cur.fetchall()]
    return {"run_ids": ids[:limit], "next_before": ids[limit - 1] if len(ids) > limit else None}


def validate_snapshot(run: Run) -> None:
    if not valid_run_id(run.id) or not valid_run_id(run.repo_id):
        raise ValueError("invalid run/repository identity")
    for value in (run.created_at, run.updated_at):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("full run timestamps must include a timezone")
    if type(run.run_attempt) is not int or run.run_attempt < 1:
        raise ValueError("run attempt must be positive")
    if not isinstance(run.head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", run.head_sha):
        raise ValueError("run head SHA is invalid")
    if not isinstance(run.status, str) or not run.status:
        raise ValueError("full run status is missing")


def refresh_stored_run(conn: psycopg.Connection, run: Run) -> str:
    """Lock only while persisting. Caller commits one refresh; no API request belongs here."""
    validate_snapshot(run)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT repo_id, head_sha, updated_at, run_attempt, status FROM run"
                    " WHERE id = %s FOR UPDATE", (run.id,))
        old = cur.fetchone()
        if old is None:
            return "missing"
        if old["repo_id"] != run.repo_id:
            raise ValueError("run belongs to a different repository")
        if (run.run_attempt < old["run_attempt"]
                or (old["updated_at"] is not None and run.updated_at < old["updated_at"])
                or (run.run_attempt == old["run_attempt"] and old["status"] == "completed"
                    and run.status != "completed")):
            return "stale"
        if (re.fullmatch(r"[0-9a-f]{40}", old["head_sha"])
                and old["head_sha"] != run.head_sha):
            raise ValueError("stored run head SHA disagrees with the snapshot")
        # Reuse the normal ingest persistence path, including normalized PR association
        # capture when that independent feature is installed. No job/step/log writes.
        upsert_run(conn, run)
        cur.execute(
            """UPDATE run SET
               workflow_id = coalesce(%s, workflow_id), workflow_path = coalesce(%s, workflow_path),
               workflow_name = coalesce(%s, workflow_name), run_number = coalesce(%s, run_number),
               event = coalesce(%s, event), head_sha = %s, head_branch = coalesce(%s, head_branch),
               created_at = %s
               WHERE id = %s AND repo_id = %s""",
            (run.workflow_id, run.workflow_path, run.workflow_name, run.run_number,
             run.event, run.head_sha, run.head_branch, run.created_at, run.id, run.repo_id),
        )
    return "refreshed"


class RunMetadataProvider(Protocol):
    async def fetch_run(self, repo: Repo, run_id: int) -> Run: ...


@dataclass
class RefreshReport:
    selected: list[int]
    outcomes: list[dict] = field(default_factory=list)
    stopped: dict | None = None

    def as_dict(self) -> dict:
        return {"selected_run_ids": self.selected, "outcomes": self.outcomes,
                "counts": dict(Counter(row["status"] for row in self.outcomes)),
                "remaining_run_ids": self.selected[len(self.outcomes):],
                "stopped": self.stopped}


async def refresh_metadata(
    provider: RunMetadataProvider, repo: Repo, run_ids: list[int],
    persist: Callable[[Run], str],
) -> RefreshReport:
    """Sequential bounded reads, with resumable stop on rate/access/transport errors."""
    ids = list(dict.fromkeys(run_ids))
    if len(ids) > MAX_REFRESH or any(not valid_run_id(n) for n in ids):
        raise ValueError("refresh requires at most 500 positive run IDs")
    report = RefreshReport(ids)
    for run_id in ids:
        try:
            run = await provider.fetch_run(repo, run_id)
            if run.id != run_id or run.repo_id != repo.id:
                raise ValueError("provider returned a different run/repository")
            validate_snapshot(run)
        except (NotFound, Expired):
            report.outcomes.append({"run_id": run_id, "status": "unavailable"})
            continue
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            report.outcomes.append({"run_id": run_id, "status": "invalid_metadata",
                                    "detail": str(exc)})
            continue
        except (RateLimited, AccessDenied, httpx.HTTPError) as exc:
            reason = ("rate_limited" if isinstance(exc, RateLimited) else
                      "access_denied" if isinstance(exc, AccessDenied) else "transport_error")
            report.stopped = {"run_id": run_id, "reason": reason, "detail": str(exc)}
            if isinstance(exc, RateLimited):
                report.stopped["retry_after_seconds"] = exc.retry_after_seconds
            break
        # This callback opens/commits its own short transaction; no connection spans await.
        try:
            status = persist(run)
        except (psycopg.Error, ValueError) as exc:
            # The callback's transaction rolls back. Keep this ID pending, alongside
            # later IDs, and still expose the earlier committed outcomes.
            report.stopped = {"run_id": run_id, "reason": "persistence_error",
                              "detail": type(exc).__name__}
            break
        report.outcomes.append({"run_id": run_id, "status": status})
    return report
