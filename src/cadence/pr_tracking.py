"""Explicit fix PR registration and evidence-bearing links to ingested runs.

Association is not a causal savings estimate. Head/branch similarity never proves a link.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from cadence.findings import FINGERPRINT_V

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class PullRequestSnapshot:
    repo_id: int
    number: int
    provider_id: int
    url: str
    head_sha: str
    base_ref: str
    state: str
    provider_updated_at: datetime
    merge_commit_sha: str | None = None
    merged_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(type(n) is not int or n <= 0 for n in (self.repo_id, self.number, self.provider_id)):
            raise ValueError("pull request identities must be positive integers")
        if self.state not in ("open", "closed", "merged"):
            raise ValueError("unknown pull request state")
        if not isinstance(self.head_sha, str) or not _SHA.fullmatch(self.head_sha):
            raise ValueError("pull request head must be an immutable SHA")
        if not isinstance(self.base_ref, str) or not self.base_ref.strip():
            raise ValueError("pull request base branch is missing")
        if (not isinstance(self.url, str) or not self.url.startswith("https://github.com/")
                or not self.url.endswith(f"/pull/{self.number}")):
            raise ValueError("pull request URL is invalid")
        if (not isinstance(self.provider_updated_at, datetime)
                or self.provider_updated_at.utcoffset() is None):
            raise ValueError("pull request update timestamp must include a timezone")
        if self.state == "merged":
            if (not isinstance(self.merge_commit_sha, str)
                    or not _SHA.fullmatch(self.merge_commit_sha)
                    or not isinstance(self.merged_at, datetime)
                    or self.merged_at.utcoffset() is None
                    or self.merged_at > self.provider_updated_at):
                raise ValueError("merged pull request requires its merge SHA and timestamp")
        elif self.merge_commit_sha is not None or self.merged_at is not None:
            raise ValueError("unmerged pull request cannot have merge evidence")

    @classmethod
    def from_github(cls, data: dict) -> PullRequestSnapshot:
        try:
            merged = data["merged"]
            state = data["state"]
            if type(merged) is not bool or state not in ("open", "closed"):
                raise ValueError("invalid pull request state")
            if merged and state != "closed":
                raise ValueError("merged pull request must be closed")
            if not merged and data.get("merged_at") is not None:
                raise ValueError("unmerged pull request has a merge timestamp")
            return cls(
                repo_id=data["base"]["repo"]["id"], number=data["number"],
                provider_id=data["id"], url=data["html_url"], head_sha=data["head"]["sha"],
                base_ref=data["base"]["ref"], state="merged" if merged else state,
                provider_updated_at=datetime.fromisoformat(data["updated_at"]),
                # Before merge this can be an ephemeral test-merge SHA: not merge evidence.
                merge_commit_sha=data.get("merge_commit_sha") if merged else None,
                merged_at=datetime.fromisoformat(data["merged_at"]) if merged else None,
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("pull request metadata is incomplete") from exc


def track_pull_request(
    conn: psycopg.Connection, snapshot: PullRequestSnapshot, finding_key: str,
) -> None:
    """Register/refresh one PR for one finding; caller owns the transaction.

    Insert first, then lock: concurrent registration of the same PR serializes even when
    the row did not exist at the start. A failed binding/revision check rolls back with the
    caller transaction. Existing evidence and finding decisions are never rewritten here.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM finding WHERE repo_id = %s AND dedupe_key = %s"
                    " AND fingerprint_v = %s",
                    (snapshot.repo_id, finding_key, FINGERPRINT_V))
        finding = cur.fetchone()
        if finding is None:
            raise ValueError("finding is not stored for this repository")
        params = {**asdict(snapshot), "finding_id": finding["id"]}
        cur.execute(
            """INSERT INTO fix_pull_request
               (repo_id, number, provider_id, finding_id, url, head_sha, base_ref, state,
                provider_updated_at, merge_commit_sha, merged_at)
               VALUES (%(repo_id)s, %(number)s, %(provider_id)s, %(finding_id)s, %(url)s,
                       %(head_sha)s, %(base_ref)s, %(state)s, %(provider_updated_at)s,
                       %(merge_commit_sha)s, %(merged_at)s)
               ON CONFLICT (repo_id, number) DO NOTHING""", params,
        )
        cur.execute("SELECT * FROM fix_pull_request WHERE repo_id = %s AND number = %s"
                    " FOR UPDATE", (snapshot.repo_id, snapshot.number))
        current = cur.fetchone()
        assert current is not None
        if current["finding_id"] != finding["id"]:
            raise ValueError("pull request already tracks a different finding")
        previous = PullRequestSnapshot(**{f.name: current[f.name] for f in fields(snapshot)})
        if previous.provider_id != snapshot.provider_id:
            raise ValueError("pull request provider identity changed")
        if previous.provider_updated_at > snapshot.provider_updated_at:
            raise ValueError("pull request snapshot is older than the stored revision")
        if previous.provider_updated_at == snapshot.provider_updated_at and previous != snapshot:
            raise ValueError("pull request snapshots conflict at the same revision")
        if previous.state == "merged" and (
            snapshot.state != "merged" or previous.merged_at != snapshot.merged_at
            or previous.merge_commit_sha != snapshot.merge_commit_sha
        ):
            raise ValueError("recorded merge evidence cannot be changed")
        cur.execute(
            """UPDATE fix_pull_request SET url = %(url)s, head_sha = %(head_sha)s,
               base_ref = %(base_ref)s, state = %(state)s,
               provider_updated_at = %(provider_updated_at)s,
               merge_commit_sha = %(merge_commit_sha)s, merged_at = %(merged_at)s,
               last_synced_at = now()
               WHERE repo_id = %(repo_id)s AND number = %(number)s""", params,
        )


def linked_runs(conn: psycopg.Connection, repo_id: int, number: int, *, limit: int = 500) -> dict:
    """Read stored metadata and run links; no API calls, writes or ancestry guesses.

    The merge path is limited to pushes of that exact commit on the PR base branch, at
    or after its merge timestamp. Later descendant commits need separate attribution.
    """
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("run limit must be between 1 and 5000")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT p.*, f.dedupe_key AS finding_key FROM fix_pull_request p"
                    " JOIN finding f ON f.id = p.finding_id AND f.repo_id = p.repo_id"
                    " WHERE p.repo_id = %s AND p.number = %s", (repo_id, number))
        tracked = cur.fetchone()
        if tracked is None:
            raise ValueError("pull request is not tracked; run cadence fix track first")
        cur.execute(
            """SELECT r.id AS run_id, r.workflow_path, r.run_attempt, r.event, r.status,
                      r.conclusion, r.head_sha, r.head_branch, r.created_at,
                      CASE WHEN r.pull_request_number = %(number)s THEN 'api_pr_number'
                           ELSE 'exact_merge_commit' END AS provenance
               FROM run r
               WHERE r.repo_id = %(repo_id)s
                 AND (r.pull_request_number = %(number)s OR
                      (%(state)s = 'merged' AND r.head_sha = %(merge_commit_sha)s
                       AND r.event = 'push' AND r.head_branch = %(base_ref)s
                       AND r.created_at >= %(merged_at)s))
               ORDER BY r.created_at DESC, r.id DESC LIMIT %(limit)s""",
            {**tracked, "limit": limit + 1},
        )
        runs = cur.fetchall()
    return {"pull_request": dict(tracked), "runs": [dict(r) for r in runs[:limit]],
            "truncated": len(runs) > limit}
