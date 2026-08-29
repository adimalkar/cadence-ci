"""Store and read back the workflow YAML an audit analysed.

Config used to be fetched live and discarded, which made three things impossible: asking
when a workflow changed, reproducing Phase 2's byte-identical round-trip test against a
fixed corpus, and re-analysing without spending API calls on bytes already seen.

Storage is content-addressed like `log_chunk`. A config edit inserts a new
`workflow_snapshot` row rather than mutating the old one, so the history of a path is just
its rows ordered by `first_seen`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row


def content_sha(content: str) -> str:
    """sha256 of the file as UTF-8 bytes, hex encoded."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    path: str
    content_sha: str
    first_seen: datetime
    last_seen: datetime


def store_snapshot(
    conn: psycopg.Connection,
    repo_id: int,
    files: dict[str, str],
    *,
    ref: str | None = None,
) -> tuple[int, int]:
    """Record the workflow files as observed now.

    Idempotent by construction: re-storing identical content bumps `last_seen` and inserts
    nothing. Returns (new_blobs, changed_paths) -- `changed_paths` counts files whose
    content differs from anything previously seen at that path, which is the number worth
    logging because it is the number that means "someone edited CI".
    """
    if not files:
        return (0, 0)

    new_blobs = 0
    changed = 0

    # Explicit dict_row: db.connect() supplies dict rows while a bare psycopg.connect()
    # supplies tuples, and this function must behave the same under both.
    with conn.cursor(row_factory=dict_row) as cur:
        for path, content in sorted(files.items()):
            sha = content_sha(content)

            cur.execute(
                "INSERT INTO workflow_blob (content_sha, content, byte_size)"
                " VALUES (%s, %s, %s) ON CONFLICT (content_sha) DO NOTHING",
                (sha, content, len(content.encode("utf-8"))),
            )
            new_blobs += cur.rowcount

            # Has this path ever been seen with different content? Checked before the
            # upsert below, or the row we are about to write would answer its own question.
            cur.execute(
                "SELECT 1 FROM workflow_snapshot"
                " WHERE repo_id = %s AND path = %s AND content_sha <> %s LIMIT 1",
                (repo_id, path, sha),
            )
            had_other = cur.fetchone() is not None

            cur.execute(
                "INSERT INTO workflow_snapshot (repo_id, path, content_sha, ref)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (repo_id, path, content_sha)"
                " DO UPDATE SET last_seen = now()"
                " RETURNING (xmax = 0) AS inserted",
                (repo_id, path, sha, ref),
            )
            row = cur.fetchone()
            inserted = bool(row["inserted"]) if row else False
            if inserted and had_other:
                changed += 1

    return (new_blobs, changed)


def load_latest(conn: psycopg.Connection, repo_id: int) -> dict[str, str]:
    """The most recently observed content for every path, as {path: content}.

    This is what an offline re-analysis reads instead of hitting the API -- and what makes
    the Phase 2 round-trip test reproducible, since it pins the corpus instead of
    re-fetching a moving HEAD.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT DISTINCT ON (s.path) s.path, b.content"
            " FROM workflow_snapshot s"
            " JOIN workflow_blob b ON b.content_sha = s.content_sha"
            " WHERE s.repo_id = %s"
            # id breaks the tie: now() is transaction time in Postgres, so rows written
            # by one store_snapshot() call share a timestamp to the microsecond.
            " ORDER BY s.path, s.last_seen DESC, s.id DESC",
            (repo_id,),
        )
        return {r["path"]: r["content"] for r in cur.fetchall()}


def history(conn: psycopg.Connection, repo_id: int, path: str) -> list[SnapshotRow]:
    """Every distinct version of one workflow file, oldest first.

    More than one row means the file changed while we were watching, and `first_seen` on
    each row bounds when. That is the "config changed here" signal Phase 3 blame wants.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT path, content_sha, first_seen, last_seen FROM workflow_snapshot"
            " WHERE repo_id = %s AND path = %s ORDER BY first_seen, id",
            (repo_id, path),
        )
        return [SnapshotRow(**r) for r in cur.fetchall()]


def changed_paths(
    conn: psycopg.Connection, repo_id: int, since: datetime
) -> list[SnapshotRow]:
    """Workflow files first seen with new content after `since`.

    Note the boundary condition: a path's very first observation is a new row too, so a
    repo's initial capture looks like a change. Callers wanting real edits should ignore
    paths with only one row -- `history()` answers that.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT path, content_sha, first_seen, last_seen FROM workflow_snapshot"
            " WHERE repo_id = %s AND first_seen > %s ORDER BY first_seen, id",
            (repo_id, since),
        )
        return [SnapshotRow(**r) for r in cur.fetchall()]
