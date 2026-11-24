"""SQL lifecycle ordering for fixed, internal ingest column references."""

from typing import Literal


def status_rank_sql(
    column: Literal["run.status", "job.status", "step.status", "EXCLUDED.status"],
) -> str:
    # These column names are constants in ingest.py, never provider/user strings.
    return (
        f"CASE {column} WHEN 'completed' THEN 2 WHEN 'in_progress' THEN 1 "
        "WHEN 'queued' THEN 0 WHEN 'waiting' THEN 0 WHEN 'pending' THEN 0 "
        "WHEN 'requested' THEN 0 ELSE -1 END"
    )


def terminal_conflict_sql(table: Literal["job", "step"]) -> str:
    return (
        f"coalesce(({table}.status = 'completed' AND EXCLUDED.status = 'completed' "
        f"AND {table}.conclusion IS NOT NULL AND EXCLUDED.conclusion IS NOT NULL "
        f"AND {table}.conclusion <> EXCLUDED.conclusion), false)"
    )


def advance_sql(table: Literal["job", "step"]) -> str:
    old = status_rank_sql("job.status" if table == "job" else "step.status")
    return f"({status_rank_sql('EXCLUDED.status')}) > ({old})"
