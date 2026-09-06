"""Silencing a finding, and recording why.

Cadence has carried `finding.status = 'suppressed'`, `suppress_scope`, `suppressed_by` and
`suppressed_reason` since migration 001, and `findings.py` has always preserved a
suppression across re-audits. **Nothing ever wrote them.** There was no ignore file, no
inline comment, and no CLI verb — every part of the mechanism existed except the part a
person touches (CAVEATS 37).

That made Phase 2's anti-spam rule 3 — *"a closed PR permanently suppresses that finding at
`rule_repo` scope"* — unimplementable. A maintainer who declined a fix would be asked again
on the next audit, and the one after that. Re-asking a settled question is how a bot gets
muted, and that damage does not come back.

Two rules are settled here rather than left to callers:

1. **A reason is mandatory.** Enforced by a database CHECK, not by this module, for the
   same reason evidence is enforced by a trigger — a rule that lives only in review erodes.
2. **Suppression is per-rule, never global.** There is deliberately no "silence
   everything": a blanket mute is indistinguishable from uninstalling, and it hides the
   signal that a rule is miscalibrated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.rows import dict_row

IGNORE_FILE = ".cadenceignore"

Scope = Literal["finding", "rule_path", "rule_repo"]
Source = Literal["ignore_file", "inline", "cli", "closed_pr"]

# `# cadence:ignore <rule> — <reason>`, with any dash the user reaches for. The reason is
# part of the syntax rather than an optional trailing comment: a rule that can be silenced
# wordlessly will be.
_INLINE = re.compile(
    r"#\s*cadence:ignore\s+(?P<rule>[a-z0-9_]+)\s*[-—–:]+\s*(?P<reason>\S.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Rule:
    """One suppression instruction, from any surface."""

    rule: str
    reason: str
    source: Source
    workflow_path: str | None = None
    job_name: str | None = None

    @property
    def scope(self) -> Scope:
        if self.job_name:
            return "finding"
        if self.workflow_path:
            return "rule_path"
        return "rule_repo"

    def matches(self, kind: str, dedupe_key: str) -> bool:
        """Does this instruction silence a finding of `kind` with `dedupe_key`?

        Dedupe keys are semantic by design — `rule:workflow_path:job_name`, per the note on
        `finding.dedupe_key` — so scope matching is a prefix test on the parts that were
        specified, and editing a workflow does not orphan a suppression.
        """
        if kind != self.rule:
            return False
        if self.workflow_path is None:
            return True
        parts = dedupe_key.split(":")
        if self.workflow_path not in parts:
            return False
        return self.job_name is None or self.job_name in parts


class SuppressionError(ValueError):
    """A malformed instruction. Raised rather than skipped: an ignore rule that silently
    does nothing is worse than one that fails loudly, because the user believes it worked."""


def parse_ignore_file(content: str) -> list[Rule]:
    """Parse `.cadenceignore`.

        # comments and blank lines are ignored
        no_dependency_cache — we cache in the container image instead
        long_tail_step:.github/workflows/ci.yml — e2e is slow on purpose
        no_run_cancellation:.github/workflows/release.yml:publish — must never be cancelled

    Left of the dash is `rule[:workflow_path[:job_name]]`; right of it is the reason, which
    is required.
    """
    rules: list[Rule] = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Split on the first dash so a reason may itself contain dashes.
        m = re.match(r"^(?P<target>\S+)\s*[-—–]+\s*(?P<reason>\S.*)$", line)
        if not m:
            raise SuppressionError(
                f"{IGNORE_FILE}:{lineno}: expected 'rule[:path[:job]] — reason', got {line!r}. "
                "A reason is required; a suppression without one becomes a permanent mystery."
            )
        target = m.group("target").split(":")
        if not target[0]:
            raise SuppressionError(f"{IGNORE_FILE}:{lineno}: empty rule name")
        rules.append(
            Rule(
                rule=target[0],
                workflow_path=target[1] if len(target) > 1 and target[1] else None,
                job_name=target[2] if len(target) > 2 and target[2] else None,
                reason=m.group("reason").strip(),
                source="ignore_file",
            )
        )
    return rules


def parse_inline(workflow_path: str, content: str) -> list[Rule]:
    """Find `# cadence:ignore <rule> — <reason>` comments in one workflow file.

    Scoped to the file it appears in. Deliberately not scoped to the nearest job: inferring
    which job a comment belongs to means guessing at YAML structure from a line number, and
    a suppression that silently covers more than the author meant is the failure mode with
    real cost.
    """
    out: list[Rule] = []
    for line in content.splitlines():
        m = _INLINE.search(line)
        if m:
            out.append(
                Rule(
                    rule=m.group("rule").lower(),
                    reason=m.group("reason").strip(),
                    source="inline",
                    workflow_path=workflow_path,
                )
            )
    return out


def collect(workflow_files: dict[str, str], ignore_file: str | None = None) -> list[Rule]:
    """Every suppression declared in the repository itself."""
    rules: list[Rule] = []
    if ignore_file:
        rules.extend(parse_ignore_file(ignore_file))
    for path, content in sorted(workflow_files.items()):
        rules.extend(parse_inline(path, content))
    return rules


def apply(
    conn: psycopg.Connection, repo_id: int, rules: list[Rule], *, commit_sha: str
) -> int:
    """Mark matching findings suppressed. Returns how many changed.

    Findings are suppressed, never withheld from the database. The ledger stays complete so
    "what is silenced here, and why" is answerable, and so un-suppressing is possible
    without re-running a detector.
    """
    if not rules:
        return 0
    changed = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, kind, dedupe_key, status FROM finding WHERE repo_id = %s",
            (repo_id,),
        )
        rows = cur.fetchall()

        for row in rows:
            match = next(
                (r for r in rules if r.matches(row["kind"], row["dedupe_key"])), None
            )
            if match is None or row["status"] == "suppressed":
                continue
            cur.execute(
                """
                UPDATE finding SET status = 'suppressed', suppress_scope = %s,
                       suppressed_reason = %s, suppress_source = %s,
                       suppressed_at = now(), last_seen_commit = %s
                WHERE id = %s
                """,
                (match.scope, match.reason, match.source, commit_sha, row["id"]),
            )
            changed += 1
    conn.commit()
    return changed


def suppress_one(
    conn: psycopg.Connection,
    finding_id: str,
    *,
    reason: str,
    scope: Scope = "finding",
    source: Source = "cli",
) -> bool:
    """Suppress a single finding by id. Returns False if no such finding."""
    if not reason.strip():
        raise SuppressionError(
            "a reason is required — without one nobody can tell later whether the finding "
            "was wrong, already fixed, or merely inconvenient"
        )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE finding SET status = 'suppressed', suppress_scope = %s,
                   suppressed_reason = %s, suppress_source = %s, suppressed_at = now()
            WHERE id = %s RETURNING id
            """,
            (scope, reason.strip(), source, finding_id),
        )
        ok = cur.fetchone() is not None
    conn.commit()
    return ok


def unsuppress_one(conn: psycopg.Connection, finding_id: str) -> bool:
    """Return a suppressed finding to `new`, clearing the suppression record.

    Back to `new` rather than `acknowledged`: un-suppressing means the decision is being
    reconsidered, and the finding should argue for itself again from the top.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE finding SET status = 'new', suppress_scope = NULL,
                   suppressed_reason = NULL, suppress_source = NULL, suppressed_at = NULL
            WHERE id = %s AND status = 'suppressed' RETURNING id
            """,
            (finding_id,),
        )
        ok = cur.fetchone() is not None
    conn.commit()
    return ok


def list_suppressed(conn: psycopg.Connection, repo_id: int) -> list[dict]:
    """What is silenced in this repo, and why — the query that keeps suppression from
    becoming a quiet uninstall."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, kind, title, suppress_scope, suppress_source,
                   suppressed_reason, suppressed_at
            FROM finding WHERE repo_id = %s AND status = 'suppressed'
            ORDER BY suppressed_at DESC NULLS LAST, id
            """,
            (repo_id,),
        )
        return list(cur.fetchall())
