"""Real database checks for atomic registration and conservative run association."""

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.db import apply_migrations
from cadence.pr_tracking import PullRequestSnapshot, linked_runs, track_pull_request

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
NOW = datetime(2026, 11, 17, tzinfo=UTC)
HEAD, MERGE = "a" * 40, "b" * 40


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
        rid = 900_000_000 + uuid.uuid4().int % 100_000_000
        conn.execute("INSERT INTO repo (id, owner, name) VALUES (%s, 'tracking', %s)",
                     (rid, str(rid)))
        keys = ["no_dependency_cache:ci.yml:test", "no_run_cancellation:ci.yml"]
        ids = []
        for key in keys:
            fid = uuid.uuid4()
            conn.execute(
                """INSERT INTO finding
                   (id, repo_id, module, kind, severity, confidence, dedupe_key,
                    first_seen_commit, last_seen_commit, title, detector_version)
                   VALUES (%s, %s, 'waste', 'no_dependency_cache', 3, .8, %s,
                           'abc', 'abc', 'finding', 'test@1')""", (fid, rid, key),
            )
            conn.execute("INSERT INTO evidence (finding_id, kind, run_ids)"
                         " VALUES (%s, 'run_history', ARRAY[1]::bigint[])",
                         (fid,))
            ids.append(fid)
        pr = PullRequestSnapshot(
            rid, 42, rid * 100, "https://github.com/tracking/widget/pull/42",
            HEAD, "main", "open", NOW,
        )
        yield conn, rid, keys, ids, pr
        conn.rollback()


def add_run(conn, rid, run_id, **over):
    data = {"event": "pull_request", "pr": 42, "sha": HEAD, "branch": "feature",
            "created": NOW - timedelta(days=1), **over}
    conn.execute(
        """INSERT INTO run
           (id, repo_id, workflow_path, event, status, conclusion, head_sha,
            head_branch, pull_request_number, created_at)
           VALUES (%s, %s, 'ci.yml', %s, 'completed', 'success', %s, %s, %s, %s)""",
        (run_id, rid, data["event"], data["sha"], data["branch"], data["pr"], data["created"]),
    )


def test_registration_is_idempotent_and_preserves_finding_decisions(seeded):
    conn, rid, keys, ids, pr = seeded
    conn.execute("UPDATE finding SET status = 'acknowledged' WHERE id = %s", (ids[0],))
    track_pull_request(conn, pr, keys[0])
    track_pull_request(conn, pr, keys[0])
    row = linked_runs(conn, rid, 42)
    assert row["pull_request"]["finding_id"] == ids[0]
    assert row["runs"] == [] and not row["truncated"]
    assert conn.execute("SELECT count(*) AS n FROM fix_pull_request WHERE repo_id = %s",
                        (rid,)).fetchone()["n"] == 1
    assert conn.execute("SELECT status FROM finding WHERE id = %s",
                        (ids[0],)).fetchone()["status"] == "acknowledged"


def test_one_pr_cannot_be_rebound_to_a_different_finding(seeded):
    conn, rid, keys, ids, pr = seeded
    track_pull_request(conn, pr, keys[0])
    with conn.transaction(), pytest.raises(ValueError, match="different finding"):
        track_pull_request(conn, pr, keys[1])
    assert linked_runs(conn, rid, 42)["pull_request"]["finding_id"] == ids[0]


def test_unknown_finding_is_rejected_and_leaves_no_registration(seeded):
    conn, rid, _, _, pr = seeded
    with pytest.raises(ValueError, match="not stored"):
        track_pull_request(conn, pr, "missing")
    assert conn.execute("SELECT count(*) AS n FROM fix_pull_request WHERE repo_id = %s",
                        (rid,)).fetchone()["n"] == 0


def test_refresh_tracks_close_reopen_and_merge_but_rejects_stale_or_conflicting_metadata(seeded):
    conn, rid, keys, _, pr = seeded
    track_pull_request(conn, pr, keys[0])
    closed = replace(pr, state="closed", provider_updated_at=NOW + timedelta(seconds=1))
    track_pull_request(conn, closed, keys[0])
    reopened = replace(pr, provider_updated_at=NOW + timedelta(seconds=2))
    track_pull_request(conn, reopened, keys[0])
    for bad in (pr, replace(reopened, head_sha="c" * 40)):
        with pytest.raises(ValueError, match="older|conflict"):
            track_pull_request(conn, bad, keys[0])
    merged = replace(pr, state="merged", merge_commit_sha=MERGE, merged_at=NOW,
                     provider_updated_at=NOW + timedelta(seconds=3))
    track_pull_request(conn, merged, keys[0])
    for bad in (replace(reopened, provider_updated_at=NOW + timedelta(seconds=4)),
                replace(merged, merge_commit_sha="c" * 40,
                        provider_updated_at=NOW + timedelta(seconds=4))):
        with pytest.raises(ValueError, match="merge evidence"):
            track_pull_request(conn, bad, keys[0])
    assert linked_runs(conn, rid, 42)["pull_request"]["state"] == "merged"


def test_links_use_api_number_or_exact_merge_commit_with_branch_event_time_guards(seeded):
    conn, rid, keys, _, pr = seeded
    merged = replace(pr, state="merged", merge_commit_sha=MERGE, merged_at=NOW)
    track_pull_request(conn, merged, keys[0])
    base = rid * 1000
    add_run(conn, rid, base)  # API association survives force-pushed PR head changes
    add_run(conn, rid, base + 1, sha="c" * 40)
    for i, over in enumerate([
        {}, {"branch": "other"}, {"event": "workflow_dispatch"},
        {"created": NOW - timedelta(seconds=1)}, {"sha": "d" * 40},
    ], 2):
        add_run(conn, rid, base + i, **{
            "event": "push", "pr": None, "sha": MERGE, "branch": "main", "created": NOW, **over,
        })
    result = linked_runs(conn, rid, 42)
    assert {r["run_id"]: r["provenance"] for r in result["runs"]} == {
        base: "api_pr_number", base + 1: "api_pr_number", base + 2: "exact_merge_commit",
    }
    assert all(r["workflow_path"] == "ci.yml" for r in result["runs"])


def test_unmerged_test_sha_and_shared_branch_or_head_never_prove_association(seeded):
    conn, rid, keys, _, pr = seeded
    track_pull_request(conn, pr, keys[0])
    add_run(conn, rid, rid * 1000, pr=None)
    add_run(conn, rid, rid * 1000 + 1, pr=99)
    assert linked_runs(conn, rid, 42)["runs"] == []


def test_run_limit_is_explicit_and_order_is_stable(seeded):
    conn, rid, keys, _, pr = seeded
    track_pull_request(conn, pr, keys[0])
    for i in range(3):
        add_run(conn, rid, rid * 1000 + i)
    result = linked_runs(conn, rid, 42, limit=2)
    assert result["truncated"]
    assert [r["run_id"] for r in result["runs"]] == [rid * 1000 + 2, rid * 1000 + 1]
    assert not linked_runs(conn, rid, 42, limit=3)["truncated"]


def test_repository_foreign_key_and_run_lookup_prevent_cross_repo_links(seeded):
    conn, rid, keys, ids, pr = seeded
    other = rid + 1
    conn.execute("INSERT INTO repo (id, owner, name) VALUES (%s, 'tracking', %s)",
                 (other, str(other)))
    track_pull_request(conn, pr, keys[0])
    add_run(conn, other, other * 1000)
    assert linked_runs(conn, rid, 42)["runs"] == []
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            """INSERT INTO fix_pull_request
               (repo_id, number, provider_id, finding_id, url, head_sha, base_ref, state,
                provider_updated_at)
               VALUES (%s, 43, %s, %s, 'url', %s, 'main', 'open', %s)""",
            (other, other * 100, ids[0], HEAD, NOW),
        )


def test_provider_identity_and_unknown_tracking_are_guarded(seeded):
    conn, rid, keys, _, pr = seeded
    with pytest.raises(ValueError, match="not tracked"):
        linked_runs(conn, rid, 42)
    track_pull_request(conn, pr, keys[0])
    with pytest.raises(ValueError, match="provider identity"):
        track_pull_request(conn, replace(pr, provider_id=pr.provider_id + 1,
                                         provider_updated_at=NOW + timedelta(seconds=1)), keys[0])
    assert linked_runs(conn, rid, 42)["pull_request"]["provider_id"] == pr.provider_id
