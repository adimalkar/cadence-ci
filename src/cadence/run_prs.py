"""Preserve every reported run/PR association without interpreting omission as absence."""

from __future__ import annotations

from typing import TypeGuard

import psycopg
from psycopg.rows import dict_row

from cadence.models import Run

MAX_PR_NUMBER = 2**31 - 1  # the existing run scalar and normalized SQL integer column
PAYLOAD_STATES = ("unknown", "empty", "reported", "malformed")


def valid_number(value: object) -> TypeGuard[int]:
    return type(value) is int and 1 <= value <= MAX_PR_NUMBER


def parse_pr_references(raw: object) -> tuple[list[int], str]:
    """Decode reported numbers, retaining valid entries in an otherwise malformed list.

    'empty' describes only the received list, never proof that no PR relates to this run.
    Missing/null fields are unknown. Duplicates retain first-seen order.
    """
    if raw is None:
        return [], "unknown"
    if not isinstance(raw, list):
        return [], "malformed"
    if not raw:
        return [], "empty"
    numbers: dict[int, None] = {}
    malformed = False
    for entry in raw:
        value = entry.get("number") if isinstance(entry, dict) else None
        if valid_number(value):
            numbers[value] = None
        else:
            malformed = True
    return list(numbers), "malformed" if malformed else "reported"


def references_for_run(run: Run) -> tuple[dict[int, str], int | None]:
    """Validate before upserting the run; retain scalar-only callers as legacy evidence."""
    if run.pr_payload_state not in PAYLOAD_STATES:
        raise ValueError("invalid PR payload state")
    if not isinstance(run.pull_request_numbers, list):
        raise ValueError("reported PR numbers must be a list")
    refs: dict[int, str] = {}
    scalar = run.pull_request_number
    if scalar is not None:
        if not valid_number(scalar):
            raise ValueError("invalid legacy PR number")
        refs[scalar] = "legacy"
    for number in run.pull_request_numbers:
        if not valid_number(number):
            raise ValueError("invalid reported PR number")
        refs[number] = "provider"
    return refs, scalar if scalar is not None else next(iter(refs), None)


def store_references(
    conn: psycopg.Connection, repo_id: int, run_id: int, refs: dict[int, str],
) -> None:
    """Append positive evidence in the caller's ingest transaction; never delete on omission."""
    with conn.cursor() as cur:
        for number, source in refs.items():
            cur.execute(
                """INSERT INTO run_pull_request (repo_id, run_id, number, source)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (run_id, number) DO UPDATE SET
                       source = CASE WHEN run_pull_request.source = 'provider'
                                          OR EXCLUDED.source = 'provider'
                                     THEN 'provider' ELSE 'legacy' END,
                       last_recorded_at = now()""",
                (repo_id, run_id, number, source),
            )


def runs_for_pr(
    conn: psycopg.Connection, repo_id: int, number: int, *, limit: int = 500,
) -> dict:
    """Read positive provider/legacy links, without head/branch or ancestry heuristics."""
    if not valid_number(number):
        raise ValueError("PR number must be a positive SQL integer")
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT r.id AS run_id, r.workflow_path, r.run_attempt, r.event, r.status,
                      r.conclusion, r.head_sha, r.head_branch, r.created_at,
                      r.pr_payload_state, p.source, p.first_recorded_at, p.last_recorded_at
               FROM run_pull_request p
               JOIN run r ON r.repo_id = p.repo_id AND r.id = p.run_id
               WHERE p.repo_id = %s AND p.number = %s
               ORDER BY r.created_at DESC, r.id DESC LIMIT %s""",
            (repo_id, number, limit + 1),
        )
        rows = cur.fetchall()
    return {"repo_id": repo_id, "number": number, "runs": [dict(r) for r in rows[:limit]],
            "truncated": len(rows) > limit, "absence_is_unknown": True}
