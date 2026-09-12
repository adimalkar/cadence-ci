"""What each commit changed, kept rather than discarded.

`enrich_changed_paths` in `audit.py` fetches changed files per commit, hands them to the
detectors in memory, and drops them. Its dedupe cache is function-local, so a second audit
re-pays the full cost and the corpus sweep can never afford it. The measured consequence is
that `irrelevant_path_trigger` fires on **0 of 51 repos** — `evalsweep` never calls the
enrichment, so the rule has no data and correctly stays silent (CAVEATS 44, 45).

This module makes the fetch a one-time cost per commit instead of per audit, which is what
turns the path rules from opt-in-behind-a-flag into always-on.

**One request, three payloads.** `GET /repos/{owner}/{repo}/commits/{sha}` returns the
changed file list, the tree sha, and the authored timestamp. So the same backfill that
feeds the path rules also fills `run.tree_sha` — NULL on all 29,134 rows since migration
001, with a partial index built for it and never used, despite the schema calling it "the
strongest flaky label: same tree, different outcome".

**What this deliberately does not do.** It stores paths, not contents. Cadence does not
read source (`PRODUCT.md` §3), and a path list is metadata about *which* files a change
touched — the same class of fact as a workflow path or a job name. Nothing here parses,
interprets, or executes anything from the repository under analysis.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from cadence.models import CommitRecord


def store_commits(
    conn: psycopg.Connection, repo_id: int, records: list[CommitRecord]
) -> int:
    """Upsert commit records. Returns how many rows were written.

    Idempotent: re-storing a commit refreshes `fetched_at` and fills in anything that was
    NULL, but never overwrites a known value with a NULL from a thinner payload — the same
    coalesce discipline `ingest.py` uses for `run`.
    """
    if not records:
        return 0
    with conn.cursor(row_factory=dict_row) as cur:
        for rec in records:
            cur.execute(
                """
                INSERT INTO repo_commit (
                    repo_id, sha, tree_sha, paths, path_count, truncated, authored_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (repo_id, sha) DO UPDATE SET
                    tree_sha    = coalesce(EXCLUDED.tree_sha, repo_commit.tree_sha),
                    paths       = EXCLUDED.paths,
                    path_count  = EXCLUDED.path_count,
                    truncated   = EXCLUDED.truncated,
                    authored_at = coalesce(EXCLUDED.authored_at, repo_commit.authored_at),
                    fetched_at  = now()
                """,
                (
                    repo_id, rec.sha, rec.tree_sha, list(rec.paths),
                    len(rec.paths), rec.truncated, rec.authored_at,
                ),
            )
    conn.commit()
    return len(records)


def unfetched_shas(
    conn: psycopg.Connection, repo_id: int, *, window_days: int = 90, limit: int = 500
) -> list[str]:
    """Head shas seen in runs that we have no commit record for yet.

    Ordered newest-first: a partial backfill should cover recent history, which is what
    both the path rules and any change-rate measure care about.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (r.head_sha) r.head_sha, r.created_at
            FROM run r
            LEFT JOIN repo_commit c ON c.repo_id = r.repo_id AND c.sha = r.head_sha
            WHERE r.repo_id = %s
              AND r.created_at > now() - make_interval(days => %s)
              AND c.sha IS NULL
            ORDER BY r.head_sha, r.created_at DESC
            """,
            (repo_id, window_days),
        )
        rows = cur.fetchall()
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return [r["head_sha"] for r in rows[:limit]]


def load_changed_paths(
    conn: psycopg.Connection, repo_id: int, run_ids: list[int]
) -> dict[int, list[str]]:
    """`{run_id: [paths]}` for the given runs, from storage rather than the API.

    Runs whose commit has not been fetched are simply absent, which is the same shape
    `enrich_changed_paths` produced and what `irrelevant_path_trigger` already handles by
    measuring its own coverage.

    **Truncated commits are excluded.** A 300-file cap means the list is incomplete, and a
    rule that reasoned "none of these paths are relevant" from a partial list would be
    wrong in the one direction that matters.
    """
    if not run_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.id AS run_id, c.paths
            FROM run r
            JOIN repo_commit c ON c.repo_id = r.repo_id AND c.sha = r.head_sha
            WHERE r.repo_id = %s AND r.id = ANY(%s)
              AND NOT c.truncated AND c.path_count > 0
            """,
            (repo_id, run_ids),
        )
        return {row["run_id"]: list(row["paths"]) for row in cur.fetchall()}


def backfill_tree_shas(conn: psycopg.Connection, repo_id: int) -> int:
    """Copy tree shas we now hold onto the runs that lacked them.

    `run.tree_sha` has been NULL on every row since migration 001 while carrying a partial
    index built for it. This is what finally gives that index rows, and with it the
    same-tree/different-outcome flaky label Phase 3 depends on.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE run r SET tree_sha = c.tree_sha
            FROM repo_commit c
            WHERE c.repo_id = r.repo_id AND c.sha = r.head_sha
              AND r.repo_id = %s AND r.tree_sha IS NULL AND c.tree_sha IS NOT NULL
            RETURNING r.id
            """,
            (repo_id,),
        )
        n = len(cur.fetchall())
    conn.commit()
    return n


def coverage(
    conn: psycopg.Connection, repo_id: int, *, window_days: int = 90
) -> tuple[int, int]:
    """`(commits_fetched, commits_seen)` in the window.

    Published alongside anything derived from paths, for the same reason the critical path
    publishes its mapping coverage: a figure computed over a third of the commits is not
    wrong so much as unreadable without its denominator.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT count(DISTINCT r.head_sha) AS seen,
                   count(DISTINCT c.sha)      AS fetched
            FROM run r
            LEFT JOIN repo_commit c ON c.repo_id = r.repo_id AND c.sha = r.head_sha
            WHERE r.repo_id = %s AND r.created_at > now() - make_interval(days => %s)
            """,
            (repo_id, window_days),
        )
        row = cur.fetchone() or {}
    return (int(row.get("fetched") or 0), int(row.get("seen") or 0))
