"""Workflow config persistence, run against a real Postgres.

Skipped when CADENCE_TEST_DATABASE_URL is unset, matching test_schema.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.configstore import (
    changed_paths,
    content_sha,
    history,
    load_latest,
    store_snapshot,
)
from cadence.db.conn import apply_migrations

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")

CI_V1 = "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
CI_V2 = "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-24.04\n"
RELEASE = "name: Release\non:\n  push:\n    tags: ['v*']\n"


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no test database")


@pytest.fixture
def conn():
    apply_migrations(TEST_DB)
    # dict_row mirrors db.connect(); a tuple-row fixture let a KeyError: 0 reach
    # production because the test path never used the factory the app uses.
    with psycopg.connect(TEST_DB, row_factory=dict_row) as c:
        c.execute("DELETE FROM workflow_snapshot")
        c.execute("DELETE FROM workflow_blob")
        c.execute("DELETE FROM repo WHERE owner = 'acme'")
        c.commit()
        yield c
        c.execute("DELETE FROM workflow_snapshot")
        c.execute("DELETE FROM workflow_blob")
        c.execute("DELETE FROM repo WHERE owner = 'acme'")
        c.commit()


# repo.id is GitHub's own repo id, supplied by the caller -- there is no sequence.
REPO_ID = 900_001
OTHER_REPO_ID = 900_002


@pytest.fixture
def repo_id(conn) -> int:
    conn.execute(
        "INSERT INTO repo (id, owner, name, is_private)"
        " VALUES (%s, 'acme', 'widget', false)",
        (REPO_ID,),
    )
    conn.commit()
    return REPO_ID


class TestContentAddressing:
    def test_sha_is_stable_and_content_dependent(self):
        assert content_sha(CI_V1) == content_sha(CI_V1)
        assert content_sha(CI_V1) != content_sha(CI_V2)

    def test_identical_content_across_paths_is_stored_once(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"a.yml": CI_V1, "b.yml": CI_V1})
        conn.commit()
        blobs = conn.execute("SELECT count(*) FROM workflow_blob").fetchone()["count"]
        snaps = conn.execute("SELECT count(*) FROM workflow_snapshot").fetchone()["count"]
        assert blobs == 1          # one blob
        assert snaps == 2          # referenced from two paths


class TestIdempotency:
    """The property Phase 2's round-trip test leans on: re-storing unchanged config is a
    no-op, so a corpus captured once stays byte-identical however often it is re-read."""

    def test_restoring_identical_content_inserts_nothing(self, conn, repo_id):
        first = store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        second = store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        assert first == (1, 0)     # one new blob, nothing "changed"
        assert second == (0, 0)    # nothing new at all
        assert conn.execute("SELECT count(*) FROM workflow_snapshot").fetchone()["count"] == 1

    def test_restoring_bumps_last_seen_without_moving_first_seen(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        before = history(conn, repo_id, "ci.yml")[0]

        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        after = history(conn, repo_id, "ci.yml")[0]

        assert after.first_seen == before.first_seen
        assert after.last_seen >= before.last_seen

    def test_empty_input_is_a_no_op(self, conn, repo_id):
        assert store_snapshot(conn, repo_id, {}) == (0, 0)


class TestHistory:
    def test_an_edit_creates_a_second_row_rather_than_mutating(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        _, changed = store_snapshot(conn, repo_id, {"ci.yml": CI_V2})
        conn.commit()

        rows = history(conn, repo_id, "ci.yml")
        assert changed == 1
        assert len(rows) == 2
        assert [r.content_sha for r in rows] == [content_sha(CI_V1), content_sha(CI_V2)]

    def test_first_ever_capture_is_not_reported_as_a_change(self, conn, repo_id):
        """A repo we have never seen is not a repo whose CI someone just edited."""
        _, changed = store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        assert changed == 0

    def test_reverting_to_earlier_content_reuses_its_row(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        store_snapshot(conn, repo_id, {"ci.yml": CI_V2})
        _, changed = store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()

        # A revert is a real change, but it is not a new version of the file.
        assert changed == 0
        assert len(history(conn, repo_id, "ci.yml")) == 2

    def test_history_is_scoped_to_one_path(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1, "release.yml": RELEASE})
        conn.commit()
        assert len(history(conn, repo_id, "ci.yml")) == 1
        assert len(history(conn, repo_id, "release.yml")) == 1


class TestLoadLatest:
    def test_returns_the_most_recent_content_per_path(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1, "release.yml": RELEASE})
        store_snapshot(conn, repo_id, {"ci.yml": CI_V2, "release.yml": RELEASE})
        conn.commit()

        latest = load_latest(conn, repo_id)
        assert latest == {"ci.yml": CI_V2, "release.yml": RELEASE}

    def test_round_trips_byte_identically(self, conn, repo_id):
        """Byte-identical read-back is the whole point -- Phase 2 asserts on exact bytes."""
        tricky = "name: CI\n\n# trailing spaces   \non: push\njobs: {}\n\n\n"
        store_snapshot(conn, repo_id, {"ci.yml": tricky})
        conn.commit()
        assert load_latest(conn, repo_id)["ci.yml"] == tricky

    def test_unknown_repo_returns_empty(self, conn):
        assert load_latest(conn, 999_999) == {}

    def test_repos_do_not_leak_into_each_other(self, conn, repo_id):
        other = OTHER_REPO_ID
        conn.execute(
            "INSERT INTO repo (id, owner, name, is_private)"
            " VALUES (%s, 'acme', 'other', false)",
            (other,),
        )
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        store_snapshot(conn, other, {"ci.yml": CI_V2})
        conn.commit()

        assert load_latest(conn, repo_id) == {"ci.yml": CI_V1}
        assert load_latest(conn, other) == {"ci.yml": CI_V2}


class TestChangedPaths:
    def test_finds_edits_after_a_cutoff(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        cutoff = datetime.now(UTC)

        store_snapshot(conn, repo_id, {"ci.yml": CI_V2})
        conn.commit()

        rows = changed_paths(conn, repo_id, cutoff)
        assert [r.content_sha for r in rows] == [content_sha(CI_V2)]

    def test_quiet_window_reports_nothing(self, conn, repo_id):
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()
        assert changed_paths(conn, repo_id, datetime.now(UTC) + timedelta(seconds=1)) == []


class TestForeignKeys:
    def test_deleting_a_repo_drops_its_snapshots_but_keeps_blobs(self, conn, repo_id):
        """Blobs are shared across repos, so they must survive one repo going away."""
        store_snapshot(conn, repo_id, {"ci.yml": CI_V1})
        conn.commit()

        conn.execute("DELETE FROM repo WHERE id = %s", (repo_id,))
        conn.commit()

        assert conn.execute("SELECT count(*) FROM workflow_snapshot").fetchone()["count"] == 0
        assert conn.execute("SELECT count(*) FROM workflow_blob").fetchone()["count"] == 1
