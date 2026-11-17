"""Durable all-PR capture, provenance and repository scoping against real Postgres."""

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from cadence.db import apply_migrations
from cadence.ingest import ensure_run_stub, upsert_run
from cadence.models import Run
from cadence.run_prs import runs_for_pr

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
NOW = datetime(2026, 11, 17, tzinfo=UTC)


@pytest.fixture(scope="module")
def schema():
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            pass
    except psycopg.OperationalError:
        pytest.skip("no test database")
    apply_migrations(TEST_DB)


@pytest.fixture
def seeded(schema):
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        rid = 700_000_000 + uuid.uuid4().int % 100_000_000
        conn.execute("INSERT INTO repo (id, owner, name) VALUES (%s, 'all-prs', %s)",
                     (rid, str(rid)))
        run = Run(
            id=rid * 100, repo_id=rid, workflow_id=1, workflow_path="ci.yml",
            workflow_name="CI", run_number=1, run_attempt=1, event="pull_request",
            status="completed", conclusion="success", head_sha="a" * 40,
            head_branch="feature", created_at=NOW, started_at=NOW, updated_at=NOW,
            pull_request_number=42, pull_request_numbers=[42, 43], pr_payload_state="reported",
        )
        yield conn, rid, run
        conn.rollback()


def test_every_reported_pr_is_queryable_and_reingest_is_idempotent(seeded):
    conn, rid, run = seeded
    upsert_run(conn, run)
    upsert_run(conn, run)
    for number in (42, 43):
        result = runs_for_pr(conn, rid, number)
        assert len(result["runs"]) == 1
        assert result["runs"][0]["source"] == "provider"
        assert result["runs"][0]["run_id"] == run.id
        assert result["absence_is_unknown"] and not result["truncated"]
    assert conn.execute("SELECT count(*) AS n FROM run_pull_request WHERE repo_id = %s",
                        (rid,)).fetchone()["n"] == 2


@pytest.mark.parametrize("state", ["unknown", "empty", "malformed"])
def test_partial_updates_do_not_erase_previous_associations(seeded, state):
    conn, rid, run = seeded
    upsert_run(conn, run)
    upsert_run(conn, replace(run, pull_request_number=None, pull_request_numbers=[],
                            pr_payload_state=state))
    for number in (42, 43):
        row, = runs_for_pr(conn, rid, number)["runs"]
        assert row["source"] == "provider" and row["pr_payload_state"] == state
    assert conn.execute("SELECT pull_request_number FROM run WHERE id = %s",
                        (run.id,)).fetchone()["pull_request_number"] == 42


def test_legacy_provenance_can_be_upgraded_but_never_downgraded(seeded):
    conn, rid, run = seeded
    legacy = replace(run, pull_request_numbers=[], pr_payload_state="unknown")
    upsert_run(conn, legacy)
    assert runs_for_pr(conn, rid, 42)["runs"][0]["source"] == "legacy"
    upsert_run(conn, run)
    upsert_run(conn, legacy)
    assert runs_for_pr(conn, rid, 42)["runs"][0]["source"] == "provider"
    assert runs_for_pr(conn, rid, 43)["runs"][0]["source"] == "provider"


def test_workflow_job_stub_does_not_overwrite_associations_or_payload_state(seeded):
    conn, rid, run = seeded
    upsert_run(conn, run)
    ensure_run_stub(conn, replace(run, pull_request_numbers=[], pr_payload_state="unknown"))
    row, = runs_for_pr(conn, rid, 43)["runs"]
    assert row["pr_payload_state"] == "reported" and row["source"] == "provider"


def test_later_full_payload_fills_stub_associations(seeded):
    conn, rid, run = seeded
    ensure_run_stub(conn, replace(run, pull_request_number=None, pull_request_numbers=[],
                                 pr_payload_state="unknown"))
    upsert_run(conn, run)
    assert {r["run_id"] for r in runs_for_pr(conn, rid, 43)["runs"]} == {run.id}


def test_payload_and_scalar_references_are_both_preserved(seeded):
    conn, rid, run = seeded
    upsert_run(conn, replace(run, pull_request_numbers=[43], pull_request_number=42))
    assert runs_for_pr(conn, rid, 42)["runs"][0]["source"] == "legacy"
    assert runs_for_pr(conn, rid, 43)["runs"][0]["source"] == "provider"


def test_repository_mismatch_cannot_modify_a_run_or_its_links(seeded):
    conn, rid, run = seeded
    other = rid + 1
    conn.execute("INSERT INTO repo (id, owner, name) VALUES (%s, 'all-prs', %s)",
                 (other, str(other)))
    upsert_run(conn, run)
    with pytest.raises(ValueError, match="different repository"):
        upsert_run(conn, replace(run, repo_id=other, status="queued"))
    assert runs_for_pr(conn, other, 42)["runs"] == []
    assert runs_for_pr(conn, rid, 42)["runs"][0]["status"] == "completed"
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO run_pull_request (repo_id, run_id, number, source)"
                     " VALUES (%s, %s, 99, 'provider')", (other, run.id))


@pytest.mark.parametrize("over", [
    {"pull_request_number": True}, {"pull_request_number": 0},
    {"pull_request_numbers": [43, False]}, {"pull_request_numbers": ["42"]},
    {"pr_payload_state": "complete"},
])
def test_invalid_models_fail_before_any_run_write(seeded, over):
    conn, _, run = seeded
    with pytest.raises(ValueError):
        upsert_run(conn, replace(run, **over))
    assert conn.execute("SELECT id FROM run WHERE id = %s", (run.id,)).fetchone() is None


def test_order_limit_and_unsuccessful_run_associations(seeded):
    conn, rid, run = seeded
    for i in range(3):
        upsert_run(conn, replace(run, id=run.id + i, conclusion="failure"))
    result = runs_for_pr(conn, rid, 43, limit=2)
    assert result["truncated"]
    assert [r["run_id"] for r in result["runs"]] == [run.id + 2, run.id + 1]
    assert all(r["conclusion"] == "failure" for r in result["runs"])
    assert not runs_for_pr(conn, rid, 43, limit=3)["truncated"]


def test_migration_backfills_positive_scalar_only_with_legacy_provenance(seeded):
    conn, _, _ = seeded
    name = f"pr_migration_{uuid.uuid4().hex}"
    with conn.transaction():
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(name)))
        conn.execute("CREATE TABLE run (id bigint PRIMARY KEY, repo_id bigint NOT NULL,"
                     " pull_request_number integer)")
        conn.execute("INSERT INTO run VALUES (1, 10, 42), (2, 10, NULL),"
                     " (3, 10, 0), (4, 10, -1)")
        migration = (Path(__file__).parents[1]
                     / "src/cadence/db/migrations/010_run_pull_requests.sql")
        conn.execute(migration.read_text())
        rows = conn.execute("SELECT run_id, number, source FROM run_pull_request").fetchall()
        assert rows == [{"run_id": 1, "number": 42, "source": "legacy"}]
        assert {r["pr_payload_state"] for r in conn.execute(
            "SELECT pr_payload_state FROM run").fetchall()} == {"unknown"}
        # Outer test transaction rolls the temporary schema back.


def test_legacy_sql_writers_continue_to_capture_during_mixed_version_rollout(seeded):
    conn, rid, run = seeded
    # This path intentionally bypasses new model/helper code, as an old worker does.
    conn.execute(
        """INSERT INTO run (id, repo_id, head_sha, created_at, pull_request_number)
           VALUES (%s, %s, 'legacy', %s, 42)""", (run.id, rid, NOW),
    )
    assert runs_for_pr(conn, rid, 42)["runs"][0]["source"] == "legacy"
    conn.execute("UPDATE run SET pull_request_number = 43 WHERE id = %s", (run.id,))
    conn.execute("UPDATE run SET pull_request_number = NULL WHERE id = %s", (run.id,))
    for number in (42, 43):
        assert runs_for_pr(conn, rid, number)["runs"][0]["source"] == "legacy"
    upsert_run(conn, run)
    conn.execute("UPDATE run SET pull_request_number = 42 WHERE id = %s", (run.id,))
    assert runs_for_pr(conn, rid, 42)["runs"][0]["source"] == "provider"
