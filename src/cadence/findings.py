"""Persist finding drafts, atomically, with their evidence.

The `finding_requires_evidence` trigger is DEFERRABLE INITIALLY DEFERRED, so it fires at
COMMIT rather than at INSERT. That is what lets us write the finding and its evidence in
either order inside one transaction — and it is why every write here must go through a
single commit rather than committing the finding first.

Status lifecycle is preserved across re-audits: a finding that already exists keeps its
`status` (so a user's `suppressed` decision survives), while its last-seen markers and
savings are refreshed. A finding that was `resolved` and reappears becomes `regressed`,
which is a different and higher-severity event than a new one.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from cadence.cost import CostContext
from cadence.detectors.base import FindingDraft

FINGERPRINT_V = 1


def persist_findings(
    conn: psycopg.Connection,
    repo_id: int,
    drafts: list[FindingDraft],
    *,
    commit_sha: str,
    cost: CostContext,
) -> dict[str, int]:
    """Write drafts in one transaction. Returns counts by outcome."""
    stats = {"new": 0, "updated": 0, "regressed": 0}

    with conn.cursor(row_factory=dict_row) as cur:
        for draft in drafts:
            cur.execute(
                "SELECT id, status FROM finding"
                " WHERE repo_id = %s AND dedupe_key = %s AND fingerprint_v = %s",
                (repo_id, draft.dedupe_key, FINGERPRINT_V),
            )
            existing = cur.fetchone()

            seconds = draft.savings.seconds_per_run if draft.savings else None
            basis = draft.savings.basis.value if draft.savings else None
            dollars = (
                cost.dollars_per_month(seconds, parallel_jobs=draft.parallel_jobs)
                if seconds
                else None
            )

            if existing is None:
                cur.execute(
                    """
                    INSERT INTO finding (
                        repo_id, module, kind, severity, confidence, dedupe_key,
                        fingerprint_v, status, first_seen_commit, last_seen_commit,
                        title, suggested_action, detector_version,
                        est_seconds_saved_per_run, est_dollars_per_month,
                        savings_basis, rate_card_version
                    ) VALUES (
                        %(repo_id)s, %(module)s, %(kind)s, %(severity)s, %(confidence)s,
                        %(dedupe_key)s, %(fpv)s, 'new', %(sha)s, %(sha)s,
                        %(title)s, %(action)s, %(dv)s,
                        %(seconds)s, %(dollars)s, %(basis)s, %(rcv)s
                    ) RETURNING id
                    """,
                    {
                        "repo_id": repo_id, "module": draft.module, "kind": draft.kind,
                        "severity": draft.severity, "confidence": draft.confidence,
                        "dedupe_key": draft.dedupe_key, "fpv": FINGERPRINT_V,
                        "sha": commit_sha, "title": draft.title,
                        "action": draft.suggested_action, "dv": draft.detector_version,
                        "seconds": seconds, "dollars": dollars, "basis": basis,
                        "rcv": cost.rate_card.version,
                    },
                )
                finding_id = cur.fetchone()["id"]
                stats["new"] += 1
            else:
                finding_id = existing["id"]
                # A resolved finding that came back is a distinct, higher-signal event --
                # nobody else can surface it because nobody else keeps finding identity
                # stable across commits.
                new_status = "regressed" if existing["status"] == "resolved" else existing["status"]
                if new_status == "regressed":
                    stats["regressed"] += 1
                else:
                    stats["updated"] += 1

                cur.execute(
                    """
                    UPDATE finding SET
                        severity = %(severity)s, confidence = %(confidence)s,
                        title = %(title)s, suggested_action = %(action)s,
                        detector_version = %(dv)s, last_seen_commit = %(sha)s,
                        last_seen_at = now(), status = %(status)s,
                        est_seconds_saved_per_run = %(seconds)s,
                        est_dollars_per_month = %(dollars)s,
                        savings_basis = %(basis)s, rate_card_version = %(rcv)s,
                        resolved_at = NULL
                    WHERE id = %(id)s
                    """,
                    {
                        "severity": draft.severity, "confidence": draft.confidence,
                        "title": draft.title, "action": draft.suggested_action,
                        "dv": draft.detector_version, "sha": commit_sha,
                        "status": new_status, "seconds": seconds, "dollars": dollars,
                        "basis": basis, "rcv": cost.rate_card.version, "id": finding_id,
                    },
                )
                # Evidence is regenerated wholesale: it describes the current state, and
                # a stale range pointing at a line that has since moved is worse than none.
                cur.execute("DELETE FROM evidence WHERE finding_id = %s", (finding_id,))

            for ev in draft.evidence:
                cur.execute(
                    """
                    INSERT INTO evidence (finding_id, kind, file_path, line_start,
                                          line_end, run_ids, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        finding_id, ev.kind, ev.file_path, ev.line_start, ev.line_end,
                        ev.run_ids or None,
                        Json(ev.payload) if ev.payload is not None else None,
                    ),
                )

    conn.commit()  # the evidence trigger fires here, not before
    return stats


def resolve_missing(
    conn: psycopg.Connection, repo_id: int, seen_keys: set[str], *, commit_sha: str
) -> int:
    """Mark findings that no longer reproduce as resolved.

    Suppressed findings are left alone — a user's decision to silence something must not
    be quietly overwritten by a detector that stopped firing for an unrelated reason.
    """
    if not seen_keys:
        return 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE finding SET status = 'resolved', resolved_at = now(),
                               last_seen_commit = %s
            WHERE repo_id = %s
              AND status IN ('new', 'acknowledged', 'regressed')
              AND NOT (dedupe_key = ANY(%s))
            RETURNING id
            """,
            (commit_sha, repo_id, list(seen_keys)),
        )
        n = len(cur.fetchall())
    conn.commit()
    return n
