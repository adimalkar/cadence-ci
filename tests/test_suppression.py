"""Suppression: the surfaces, and the two rules that are not negotiable.

The parsing is small. What these tests protect is the discipline around it — a reason is
mandatory (enforced by the database, not by code), scope is never global, and a suppressed
finding survives a re-audit. That last one is what makes Phase 2's anti-spam rule 3
implementable at all: a declined fix must not come back on the next audit.
"""

from __future__ import annotations

import pytest

from cadence.suppress import (
    IGNORE_FILE,
    Rule,
    SuppressionError,
    collect,
    parse_ignore_file,
    parse_inline,
)

# --- the ignore file ---------------------------------------------------------------------


def test_scopes_come_from_how_specific_the_target_is():
    rules = parse_ignore_file(
        "no_dependency_cache — cached in the image\n"
        "long_tail_step:.github/workflows/ci.yml — e2e is slow on purpose\n"
        "no_run_cancellation:.github/workflows/release.yml:publish — never cancel a release\n"
    )
    assert [r.scope for r in rules] == ["rule_repo", "rule_path", "finding"]
    assert rules[2].job_name == "publish"
    assert rules[0].reason == "cached in the image"


def test_comments_and_blank_lines_are_skipped():
    rules = parse_ignore_file("# team decision 2026-09\n\n   \nlong_tail_step — deliberate\n")
    assert len(rules) == 1


def test_a_reason_is_required():
    """The whole point of `suppressed_reason`. Without it nobody can tell later whether the
    finding was wrong, already fixed, or merely inconvenient — so nobody dares remove it."""
    with pytest.raises(SuppressionError, match="reason is required"):
        parse_ignore_file("no_dependency_cache\n")


def test_a_malformed_line_raises_rather_than_being_skipped():
    """A rule that silently does nothing is worse than one that fails: the user believes
    it worked and stops looking."""
    with pytest.raises(SuppressionError, match=IGNORE_FILE):
        parse_ignore_file("this is not a suppression\n")


def test_a_reason_may_contain_dashes():
    (rule,) = parse_ignore_file("long_tail_step — slow on purpose — see ADR-14\n")
    assert rule.reason == "slow on purpose — see ADR-14"


def test_error_names_the_offending_line_number():
    with pytest.raises(SuppressionError, match=r":3:"):
        parse_ignore_file("# ok\nlong_tail_step — fine\nbroken line here\n")


# --- inline comments ----------------------------------------------------------------------


def test_inline_comment_is_scoped_to_its_file():
    (rule,) = parse_inline(
        ".github/workflows/ci.yml",
        "jobs:\n  test:  # cadence:ignore long_tail_step — e2e is meant to be slow\n",
    )
    assert rule.scope == "rule_path"
    assert rule.workflow_path == ".github/workflows/ci.yml"
    assert rule.source == "inline"


def test_inline_without_a_reason_is_not_a_suppression():
    """Same rule as the file: silence has to be explained, so a bare marker does nothing."""
    assert parse_inline(".github/workflows/ci.yml", "# cadence:ignore long_tail_step\n") == []


def test_collect_merges_both_surfaces():
    rules = collect(
        {".github/workflows/ci.yml": "# cadence:ignore long_tail_step — slow on purpose"},
        "no_dependency_cache — cached in the image",
    )
    assert {r.source for r in rules} == {"inline", "ignore_file"}


# --- matching -----------------------------------------------------------------------------


def test_repo_scope_matches_every_finding_of_that_rule():
    r = Rule(rule="long_tail_step", reason="x", source="cli")
    assert r.matches("long_tail_step", "long_tail_step:.github/workflows/any.yml:job")
    assert not r.matches("no_dependency_cache", "no_dependency_cache:x:y")


def test_path_scope_does_not_leak_to_another_workflow():
    r = Rule(rule="long_tail_step", reason="x", source="ignore_file",
             workflow_path=".github/workflows/ci.yml")
    assert r.matches("long_tail_step", "long_tail_step:.github/workflows/ci.yml:e2e")
    assert not r.matches("long_tail_step", "long_tail_step:.github/workflows/nightly.yml:e2e")


def test_finding_scope_does_not_leak_to_another_job():
    r = Rule(rule="no_run_cancellation", reason="x", source="ignore_file",
             workflow_path=".github/workflows/release.yml", job_name="publish")
    key = "no_run_cancellation:.github/workflows/release.yml"
    assert r.matches("no_run_cancellation", f"{key}:publish")
    assert not r.matches("no_run_cancellation", f"{key}:build")


def test_matching_survives_a_workflow_edit():
    """Dedupe keys are semantic — (rule, path, job) — precisely so editing YAML does not
    orphan a suppression. Asserted here because it is the reason for that design."""
    r = Rule(rule="long_tail_step", reason="x", source="ignore_file",
             workflow_path=".github/workflows/ci.yml")
    # Same rule and file, different job, different line numbers upstream: still silenced.
    assert r.matches("long_tail_step", "long_tail_step:.github/workflows/ci.yml:renamed-job")


# --- there is no global scope -------------------------------------------------------------


def test_there_is_no_wildcard_rule():
    """A blanket mute is indistinguishable from uninstalling, and it hides the signal that
    a rule is miscalibrated. `*` is a rule name like any other, so it silences nothing."""
    (rule,) = parse_ignore_file("* — silence everything\n")
    assert not rule.matches("long_tail_step", "long_tail_step:a:b")
    assert not rule.matches("no_run_cancellation", "no_run_cancellation:a:b")


# --- against a real database ---------------------------------------------------------------

import os  # noqa: E402

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from cadence.db.conn import apply_migrations  # noqa: E402
from cadence.suppress import apply as suppress_apply  # noqa: E402
from cadence.suppress import list_suppressed, suppress_one, unsuppress_one  # noqa: E402

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
REPO_ID = 900_101


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


db = pytest.mark.skipif(not _db_available(), reason="no test database")


@pytest.fixture
def conn():
    apply_migrations(TEST_DB)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as c:
        c.execute("DELETE FROM finding WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM repo WHERE id = %s", (REPO_ID,))
        c.execute(
            "INSERT INTO repo (id, owner, name, is_private)"
            " VALUES (%s, 'acme', 'suppressed', false)",
            (REPO_ID,),
        )
        c.commit()
        yield c
        c.execute("DELETE FROM finding WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM repo WHERE id = %s", (REPO_ID,))
        c.commit()


def _finding(conn, kind: str, dedupe_key: str) -> str:
    row = conn.execute(
        """
        INSERT INTO finding (repo_id, module, kind, severity, confidence, dedupe_key,
                             first_seen_commit, last_seen_commit, title, detector_version)
        VALUES (%s, 'waste', %s, 3, 0.9, %s, 'sha', 'sha', %s, 'v1') RETURNING id
        """,
        (REPO_ID, kind, dedupe_key, f"title for {kind}"),
    ).fetchone()
    # The finding_requires_evidence trigger is DEFERRABLE INITIALLY DEFERRED, so it fires
    # at COMMIT: a fixture that skipped evidence would fail here, correctly.
    conn.execute(
        "INSERT INTO evidence (finding_id, kind, run_ids) VALUES (%s, 'run_history', %s)",
        (row["id"], [1, 2, 3]),
    )
    conn.commit()
    return str(row["id"])


def _status(conn, fid: str) -> dict:
    return conn.execute(
        "SELECT status, suppress_scope, suppress_source, suppressed_reason,"
        " suppressed_at FROM finding WHERE id = %s",
        (fid,),
    ).fetchone()


@db
class TestDatabaseEnforcesTheRules:
    def test_a_suppression_without_a_reason_is_rejected_by_the_database(self, conn):
        """Enforced by CHECK, not by code — the same discipline as the evidence trigger.
        A rule that lives only in review erodes."""
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE finding SET status = 'suppressed', suppress_scope = 'finding',"
                " suppress_source = 'cli' WHERE id = %s",
                (fid,),
            )
        conn.rollback()

    def test_a_blank_reason_counts_as_no_reason(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE finding SET status = 'suppressed', suppress_scope = 'finding',"
                " suppress_source = 'cli', suppressed_reason = '   ' WHERE id = %s",
                (fid,),
            )
        conn.rollback()


@db
class TestApplyingRepoRules:
    def test_repo_scope_silences_every_matching_finding(self, conn):
        a = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        b = _finding(conn, "long_tail_step", "long_tail_step:nightly.yml:soak")
        c = _finding(conn, "no_dependency_cache", "no_dependency_cache:ci.yml:build")

        n = suppress_apply(
            conn, REPO_ID,
            parse_ignore_file("long_tail_step — slow on purpose"),
            commit_sha="sha",
        )
        assert n == 2
        assert _status(conn, a)["status"] == "suppressed"
        assert _status(conn, b)["status"] == "suppressed"
        assert _status(conn, c)["status"] == "new"

    def test_path_scope_leaves_other_workflows_alone(self, conn):
        a = _finding(conn, "long_tail_step", "long_tail_step:.github/workflows/ci.yml:e2e")
        b = _finding(conn, "long_tail_step", "long_tail_step:.github/workflows/nightly.yml:soak")
        suppress_apply(
            conn, REPO_ID,
            parse_ignore_file("long_tail_step:.github/workflows/ci.yml — deliberate"),
            commit_sha="sha",
        )
        assert _status(conn, a)["status"] == "suppressed"
        assert _status(conn, b)["status"] == "new"

    def test_provenance_and_reason_are_recorded(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        suppress_apply(
            conn, REPO_ID, parse_ignore_file("long_tail_step — e2e is slow on purpose"),
            commit_sha="sha",
        )
        row = _status(conn, fid)
        assert row["suppress_source"] == "ignore_file"
        assert row["suppress_scope"] == "rule_repo"
        assert row["suppressed_reason"] == "e2e is slow on purpose"
        assert row["suppressed_at"] is not None

    def test_applying_twice_is_idempotent(self, conn):
        _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        rules = parse_ignore_file("long_tail_step — deliberate")
        assert suppress_apply(conn, REPO_ID, rules, commit_sha="sha") == 1
        assert suppress_apply(conn, REPO_ID, rules, commit_sha="sha") == 0


@db
class TestTheCliVerbs:
    def test_suppress_then_unsuppress_round_trips(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        assert suppress_one(conn, fid, reason="not worth it")
        assert _status(conn, fid)["status"] == "suppressed"

        assert unsuppress_one(conn, fid)
        row = _status(conn, fid)
        # Back to `new`, not `acknowledged`: reconsidering means it argues from the top.
        assert row["status"] == "new"
        assert row["suppressed_reason"] is None
        assert row["suppress_source"] is None

    def test_suppress_refuses_an_empty_reason_before_reaching_the_database(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        with pytest.raises(SuppressionError, match="reason is required"):
            suppress_one(conn, fid, reason="   ")

    def test_unsuppressing_something_not_suppressed_is_a_no_op(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        assert unsuppress_one(conn, fid) is False

    def test_list_shows_what_is_silenced_and_why(self, conn):
        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        suppress_one(conn, fid, reason="e2e is slow on purpose")
        (row,) = list_suppressed(conn, REPO_ID)
        assert row["suppressed_reason"] == "e2e is slow on purpose"
        assert row["kind"] == "long_tail_step"


@db
class TestPhase2AntiSpamRule3:
    """"A closed PR permanently suppresses that finding at rule_repo scope."

    This was unimplementable before migration 006 — the columns existed and nothing wrote
    them. The behaviour it prevents is a maintainer being re-asked a settled question on
    every audit, which is how a bot gets muted.
    """

    def test_a_suppressed_finding_survives_a_re_audit(self, conn):
        from cadence.cost import CostContext, RateCard
        from cadence.detectors.base import EvidenceDraft, FindingDraft
        from cadence.findings import persist_findings

        fid = _finding(conn, "long_tail_step", "long_tail_step:ci.yml:e2e")
        suppress_one(conn, fid, reason="declined the fix PR", scope="rule_repo",
                     source="closed_pr")

        draft = FindingDraft(
            kind="long_tail_step", module="waste", severity=3, confidence=0.9,
            dedupe_key="long_tail_step:ci.yml:e2e", title="it is back",
            detector_version="v1",
            evidence=[EvidenceDraft(kind="run_history", run_ids=[1, 2, 3])],
        )
        cost = CostContext(
            is_private=False, dominant_labels=["ubuntu-latest"], runs_per_month=100.0,
            rate_card=RateCard(version=20260301, rates={"ubuntu-latest": 0.006},
                               free_on_public={"ubuntu-latest": True}),
        )
        persist_findings(conn, REPO_ID, [draft], commit_sha="sha2", cost=cost)

        row = _status(conn, fid)
        assert row["status"] == "suppressed", "a declined fix must not be re-proposed"
        assert row["suppressed_reason"] == "declined the fix PR"
