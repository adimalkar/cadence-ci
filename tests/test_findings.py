"""Finding persistence, resolution, and regression lifecycle tests."""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

import cadence.audit as audit_mod
import cadence.cli as cli_mod
from cadence.cost import CostContext, RateCard
from cadence.db.conn import apply_migrations
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext
from cadence.findings import persist_findings, resolve_missing
from cadence.suppress import suppress_one

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
REPO_ID = 900_301
OTHER_REPO_ID = 900_302

RATE_CARD = RateCard(
    version=2026,
    rates={"ubuntu-latest": 0.006},
    free_on_public={"ubuntu-latest": True},
)


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


db = pytest.mark.skipif(not _db_available(), reason="no test database")


def _cost() -> CostContext:
    return CostContext(
        is_private=False,
        dominant_labels=["ubuntu-latest"],
        runs_per_month=100.0,
        rate_card=RATE_CARD,
    )


def _ctx(repo_id: int = REPO_ID) -> AuditContext:
    return AuditContext(
        repo_id=repo_id,
        owner="acme",
        name="findings",
        is_private=False,
        workflows=[],
        runs=[],
        step_series={},
        cost=_cost(),
        window_days=90,
    )


def _draft(key: str, *, kind: str = "no_run_cancellation") -> FindingDraft:
    return FindingDraft(
        kind=kind,
        module="waste",
        severity=3,
        confidence=0.95,
        dedupe_key=key,
        title=f"finding for {key}",
        detector_version="v1",
        evidence=[EvidenceDraft(kind="run_history", run_ids=[1, 2, 3])],
    )


@pytest.fixture
def conn():
    apply_migrations(TEST_DB)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as c:
        for rid in (REPO_ID, OTHER_REPO_ID):
            c.execute("DELETE FROM finding WHERE repo_id = %s", (rid,))
            c.execute("DELETE FROM repo WHERE id = %s", (rid,))
        c.execute(
            "INSERT INTO repo (id, owner, name, is_private) VALUES (%s, 'acme', 'findings', false)",
            (REPO_ID,),
        )
        c.execute(
            "INSERT INTO repo (id, owner, name, is_private) VALUES (%s, 'acme', 'other', false)",
            (OTHER_REPO_ID,),
        )
        c.commit()
        yield c
        for rid in (REPO_ID, OTHER_REPO_ID):
            c.execute("DELETE FROM finding WHERE repo_id = %s", (rid,))
            c.execute("DELETE FROM repo WHERE id = %s", (rid,))
        c.commit()


def _fetch_finding(conn: psycopg.Connection, dedupe_key: str, *, repo_id: int = REPO_ID) -> dict:
    row = conn.execute(
        "SELECT id, status, resolved_at, last_seen_commit"
        " FROM finding WHERE repo_id = %s AND dedupe_key = %s",
        (repo_id, dedupe_key),
    ).fetchone()
    assert row is not None
    return row


class _OkDetector:
    def __init__(self, detector_id: str, drafts: list[FindingDraft]):
        self.id = detector_id
        self._drafts = drafts

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        return list(self._drafts)


class _FailingDetector:
    def __init__(self, detector_id: str = "waste.boom"):
        self.id = detector_id

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        raise RuntimeError("detector crashed")


# --- hermetic unit tests -----------------------------------------------------------------


class TestRunAuditResolutionGuards:
    def test_empty_drafts_resolves_when_all_detectors_succeed(self, monkeypatch):
        resolved_calls: list[tuple[int, set[str], str]] = []
        monkeypatch.setattr(audit_mod, "DETECTORS", [_OkDetector("waste.ok", [])])
        monkeypatch.setattr(
            audit_mod,
            "persist_findings",
            lambda *a, **kw: pytest.fail("persist_findings should not run for empty drafts"),
        )

        def fake_resolve(conn, repo_id, seen_keys, *, commit_sha):
            resolved_calls.append((repo_id, set(seen_keys), commit_sha))
            return 2

        monkeypatch.setattr(audit_mod, "resolve_missing", fake_resolve)

        res = audit_mod.run_audit(object(), _ctx(), commit_sha="sha-clean", persist=True)
        assert res["drafts"] == []
        assert res["persisted"] is None
        assert res["resolved"] == 2
        assert res["failed"] == []
        assert resolved_calls == [(REPO_ID, set(), "sha-clean")]

    def test_failed_detector_blocks_resolution_even_with_other_drafts(self, monkeypatch):
        draft = _draft("no_run_cancellation:.github/workflows/ci.yml")
        monkeypatch.setattr(
            audit_mod,
            "DETECTORS",
            [_OkDetector("waste.ok", [draft]), _FailingDetector("waste.broken")],
        )
        monkeypatch.setattr(
            audit_mod,
            "persist_findings",
            lambda *a, **kw: {"new": 1, "updated": 0, "regressed": 0},
        )
        monkeypatch.setattr(
            audit_mod,
            "resolve_missing",
            lambda *a, **kw: pytest.fail("resolve_missing must not run when a detector failed"),
        )

        res = audit_mod.run_audit(object(), _ctx(), commit_sha="sha-partial", persist=True)
        assert len(res["drafts"]) == 1
        assert res["persisted"] == {"new": 1, "updated": 0, "regressed": 0}
        assert res["resolved"] == 0
        assert res["failed"] == [("waste.broken", "detector crashed")]

    def test_failed_detector_blocks_resolution_when_drafts_empty(self, monkeypatch):
        monkeypatch.setattr(audit_mod, "DETECTORS", [_FailingDetector("waste.broken")])
        monkeypatch.setattr(
            audit_mod,
            "resolve_missing",
            lambda *a, **kw: pytest.fail("resolve_missing must not run when a detector failed"),
        )

        res = audit_mod.run_audit(object(), _ctx(), commit_sha="sha-fail", persist=True)
        assert res["drafts"] == []
        assert res["resolved"] == 0
        assert res["failed"] == [("waste.broken", "detector crashed")]

    def test_dry_run_skips_both_persist_and_resolve(self, monkeypatch):
        monkeypatch.setattr(audit_mod, "DETECTORS", [_OkDetector("waste.ok", [])])
        monkeypatch.setattr(
            audit_mod,
            "resolve_missing",
            lambda *a, **kw: pytest.fail("resolve_missing must not run when persist=False"),
        )

        res = audit_mod.run_audit(object(), _ctx(), commit_sha="sha-dry", persist=False)
        assert res["persisted"] is None
        assert res["resolved"] == 0


class TestRenderAuditEmptyWithResolved:
    def test_prints_resolved_count_when_zero_drafts_remain(self, capsys):
        result = {"drafts": [], "persisted": None, "resolved": 2, "failed": []}
        cli_mod._render_audit("acme/findings", _ctx(), None, result, dry_run=False)
        out = capsys.readouterr().out
        assert "No recoverable waste found." in out
        assert "resolved 2 finding(s)" in out

    def test_does_not_print_resolved_on_dry_run(self, capsys):
        result = {"drafts": [], "persisted": None, "resolved": 2, "failed": []}
        cli_mod._render_audit("acme/findings", _ctx(), None, result, dry_run=True)
        out = capsys.readouterr().out
        assert "No recoverable waste found." in out
        assert "resolved 2 finding(s)" not in out


# --- postgres integration tests ----------------------------------------------------------


@db
class TestResolveMissingDatabase:
    def test_empty_seen_keys_resolves_all_active_findings_but_keeps_suppressed(self, conn):
        k_new = "no_run_cancellation:a.yml"
        k_ack = "no_run_cancellation:b.yml"
        k_sup = "no_run_cancellation:c.yml"
        k_other = "no_run_cancellation:other.yml"

        persist_findings(
            conn,
            REPO_ID,
            [_draft(k_new), _draft(k_ack), _draft(k_sup)],
            commit_sha="sha1",
            cost=_cost(),
        )
        persist_findings(
            conn,
            OTHER_REPO_ID,
            [_draft(k_other)],
            commit_sha="sha1",
            cost=_cost(),
        )

        ack_row = _fetch_finding(conn, k_ack)
        conn.execute("UPDATE finding SET status = 'acknowledged' WHERE id = %s", (ack_row["id"],))
        conn.commit()

        sup_row = _fetch_finding(conn, k_sup)
        suppress_one(conn, str(sup_row["id"]), reason="deliberate")

        n = resolve_missing(conn, REPO_ID, set(), commit_sha="sha2")
        assert n == 2

        new_after = _fetch_finding(conn, k_new)
        assert new_after["status"] == "resolved"
        assert new_after["resolved_at"] is not None
        assert new_after["last_seen_commit"] == "sha2"

        ack_after = _fetch_finding(conn, k_ack)
        assert ack_after["status"] == "resolved"
        assert ack_after["resolved_at"] is not None

        sup_after = _fetch_finding(conn, k_sup)
        assert sup_after["status"] == "suppressed"
        assert sup_after["resolved_at"] is None

        # Other repository's findings are untouched.
        assert _fetch_finding(conn, k_other, repo_id=OTHER_REPO_ID)["status"] == "new"

        # Calling resolve_missing again is a no-op (already resolved).
        assert resolve_missing(conn, REPO_ID, set(), commit_sha="sha3") == 0

    def test_non_empty_seen_keys_only_resolves_missing_findings(self, conn):
        k_keep = "no_run_cancellation:keep.yml"
        k_gone = "no_run_cancellation:gone.yml"
        persist_findings(
            conn,
            REPO_ID,
            [_draft(k_keep), _draft(k_gone)],
            commit_sha="sha1",
            cost=_cost(),
        )

        n = resolve_missing(conn, REPO_ID, {k_keep}, commit_sha="sha2")
        assert n == 1
        assert _fetch_finding(conn, k_keep)["status"] == "new"
        assert _fetch_finding(conn, k_gone)["status"] == "resolved"

    def test_run_audit_full_lifecycle_new_resolved_regressed(self, conn, monkeypatch):
        key = "no_run_cancellation:ci.yml"
        ctx = _ctx()

        # 1. Finding appears -> new
        monkeypatch.setattr(audit_mod, "DETECTORS", [_OkDetector("waste.ok", [_draft(key)])])
        r1 = audit_mod.run_audit(conn, ctx, commit_sha="sha1", persist=True)
        assert r1["persisted"] == {"new": 1, "updated": 0, "regressed": 0}
        assert r1["resolved"] == 0
        assert _fetch_finding(conn, key)["status"] == "new"

        # 2. Last finding fixed (0 drafts) -> resolved
        monkeypatch.setattr(audit_mod, "DETECTORS", [_OkDetector("waste.ok", [])])
        r2 = audit_mod.run_audit(conn, ctx, commit_sha="sha2", persist=True)
        assert r2["persisted"] is None
        assert r2["resolved"] == 1
        resolved = _fetch_finding(conn, key)
        assert resolved["status"] == "resolved"
        assert resolved["resolved_at"] is not None

        # 3. Finding comes back -> regressed
        monkeypatch.setattr(audit_mod, "DETECTORS", [_OkDetector("waste.ok", [_draft(key)])])
        r3 = audit_mod.run_audit(conn, ctx, commit_sha="sha3", persist=True)
        assert r3["persisted"] == {"new": 0, "updated": 0, "regressed": 1}
        assert r3["resolved"] == 0
        regressed = _fetch_finding(conn, key)
        assert regressed["status"] == "regressed"
        assert regressed["resolved_at"] is None

        # 4. Detector crashes -> regressed finding is NOT resolved
        monkeypatch.setattr(audit_mod, "DETECTORS", [_FailingDetector("waste.ok")])
        r4 = audit_mod.run_audit(conn, ctx, commit_sha="sha4", persist=True)
        assert r4["resolved"] == 0
        assert len(r4["failed"]) == 1
        assert _fetch_finding(conn, key)["status"] == "regressed"
