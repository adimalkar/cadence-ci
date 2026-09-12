"""The commit store, against a real Postgres.

What this exists to protect is not the SQL — it is the two places where being wrong is
expensive:

1. **Truncated commits must never reach a consumer.** GitHub caps a commit's file list at
   300. A rule concluding "none of these paths matter" from a partial list is wrong in the
   one direction that ships bad advice.
2. **Coverage must be reportable.** A fragility figure computed over a third of a repo's
   commits is not wrong so much as unreadable without its denominator, which is the same
   discipline the critical path already applies below 80% mapping.

Skipped when CADENCE_TEST_DATABASE_URL is unset, matching test_configstore.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence.commitstore import (
    backfill_tree_shas,
    coverage,
    load_changed_paths,
    store_commits,
    unfetched_shas,
)
from cadence.db.conn import apply_migrations
from cadence.models import GITHUB_FILES_CAP, CommitRecord

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
REPO_ID = 900_201


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
    with psycopg.connect(TEST_DB, row_factory=dict_row) as c:
        c.execute("DELETE FROM repo_commit WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM run WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM repo WHERE id = %s", (REPO_ID,))
        c.execute(
            "INSERT INTO repo (id, owner, name, is_private)"
            " VALUES (%s, 'acme', 'commits', false)",
            (REPO_ID,),
        )
        c.commit()
        yield c
        c.execute("DELETE FROM repo_commit WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM run WHERE repo_id = %s", (REPO_ID,))
        c.execute("DELETE FROM repo WHERE id = %s", (REPO_ID,))
        c.commit()


def _run(conn, run_id: int, sha: str, *, days_ago: int = 1) -> int:
    conn.execute(
        """
        INSERT INTO run (id, repo_id, head_sha, created_at, run_attempt)
        VALUES (%s, %s, %s, now() - make_interval(days => %s), 1)
        """,
        (run_id, REPO_ID, sha, days_ago),
    )
    conn.commit()
    return run_id


class TestStoringAndReadingBack:
    def test_paths_survive_a_round_trip(self, conn):
        _run(conn, 1, "sha-a")
        store_commits(conn, REPO_ID, [CommitRecord(sha="sha-a", paths=["src/a.py", "README.md"])])
        assert load_changed_paths(conn, REPO_ID, [1]) == {1: ["src/a.py", "README.md"]}

    def test_re_storing_is_idempotent_and_refreshes(self, conn):
        _run(conn, 1, "sha-a")
        rec = CommitRecord(sha="sha-a", paths=["src/a.py"])
        store_commits(conn, REPO_ID, [rec])
        store_commits(conn, REPO_ID, [rec])
        row = conn.execute(
            "SELECT count(*) AS n FROM repo_commit WHERE repo_id = %s", (REPO_ID,)
        ).fetchone()
        assert row["n"] == 1

    def test_a_known_tree_sha_is_not_overwritten_by_a_thinner_payload(self, conn):
        """Same coalesce discipline `ingest.py` uses: a later NULL never erases a value."""
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], tree_sha="tree-1")])
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], tree_sha=None)])
        row = conn.execute(
            "SELECT tree_sha FROM repo_commit WHERE repo_id = %s AND sha = 's'", (REPO_ID,)
        ).fetchone()
        assert row["tree_sha"] == "tree-1"

    def test_authored_at_is_stored(self, conn):
        when = datetime.now(UTC) - timedelta(days=3)
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], authored_at=when)])
        row = conn.execute(
            "SELECT authored_at FROM repo_commit WHERE sha = 's' AND repo_id = %s", (REPO_ID,)
        ).fetchone()
        assert row["authored_at"] is not None

    def test_storing_nothing_is_not_an_error(self, conn):
        assert store_commits(conn, REPO_ID, []) == 0


class TestTruncationIsNeverHandedOn:
    def test_a_commit_at_the_cap_counts_as_truncated(self):
        """No explicit API flag exists, and a commit touching exactly 300 files is rarer
        than one touching more."""
        assert CommitRecord(sha="s", paths=[f"f{i}" for i in range(GITHUB_FILES_CAP)]).truncated
        assert not CommitRecord(sha="s", paths=[f"f{i}" for i in range(3)]).truncated

    def test_truncated_commits_are_withheld_from_consumers(self, conn):
        """The expensive failure: a rule reasoning "nothing relevant changed" from a list
        GitHub cut short."""
        _run(conn, 1, "big")
        store_commits(
            conn, REPO_ID,
            [CommitRecord(sha="big", paths=[f"f{i}.py" for i in range(GITHUB_FILES_CAP)])],
        )
        assert load_changed_paths(conn, REPO_ID, [1]) == {}

    def test_truncation_is_still_recorded_so_it_is_diagnosable(self, conn):
        store_commits(
            conn, REPO_ID,
            [CommitRecord(sha="big", paths=[f"f{i}.py" for i in range(GITHUB_FILES_CAP)])],
        )
        row = conn.execute(
            "SELECT truncated, path_count FROM repo_commit WHERE sha = 'big' AND repo_id = %s",
            (REPO_ID,),
        ).fetchone()
        assert row["truncated"] is True
        assert row["path_count"] == GITHUB_FILES_CAP

    def test_an_empty_commit_is_withheld_too(self, conn):
        """A merge commit legitimately reports no files. Absent and empty must not be
        confused by a consumer counting 'runs with known paths'."""
        _run(conn, 1, "merge")
        store_commits(conn, REPO_ID, [CommitRecord(sha="merge", paths=[])])
        assert load_changed_paths(conn, REPO_ID, [1]) == {}


class TestTheBackfillQueue:
    def test_unfetched_lists_only_what_is_missing(self, conn):
        _run(conn, 1, "have", days_ago=1)
        _run(conn, 2, "need", days_ago=1)
        store_commits(conn, REPO_ID, [CommitRecord(sha="have", paths=["a"])])
        assert unfetched_shas(conn, REPO_ID) == ["need"]

    def test_it_is_newest_first(self, conn):
        _run(conn, 1, "old", days_ago=30)
        _run(conn, 2, "new", days_ago=1)
        assert unfetched_shas(conn, REPO_ID) == ["new", "old"]

    def test_commits_outside_the_window_are_not_queued(self, conn):
        _run(conn, 1, "ancient", days_ago=400)
        assert unfetched_shas(conn, REPO_ID, window_days=90) == []

    def test_the_limit_is_honoured_so_a_pass_is_bounded(self, conn):
        for i in range(10):
            _run(conn, i + 1, f"sha-{i}", days_ago=i + 1)
        assert len(unfetched_shas(conn, REPO_ID, limit=4)) == 4

    def test_one_sha_across_many_runs_is_fetched_once(self, conn):
        """The whole point of caching: 271 runs of flask collapse to 93 commits."""
        _run(conn, 1, "same")
        _run(conn, 2, "same")
        _run(conn, 3, "same")
        assert unfetched_shas(conn, REPO_ID) == ["same"]


class TestTreeShaBackfill:
    def test_it_fills_runs_that_had_none(self, conn):
        """`run.tree_sha` was NULL on all 29,134 corpus rows since migration 001, with an
        index built for it and never used."""
        _run(conn, 1, "s")
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], tree_sha="tree-9")])
        assert backfill_tree_shas(conn, REPO_ID) == 1
        row = conn.execute("SELECT tree_sha FROM run WHERE id = 1").fetchone()
        assert row["tree_sha"] == "tree-9"

    def test_it_never_overwrites_a_tree_sha_already_present(self, conn):
        _run(conn, 1, "s")
        conn.execute("UPDATE run SET tree_sha = 'original' WHERE id = 1")
        conn.commit()
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], tree_sha="different")])
        assert backfill_tree_shas(conn, REPO_ID) == 0
        row = conn.execute("SELECT tree_sha FROM run WHERE id = 1").fetchone()
        assert row["tree_sha"] == "original"

    def test_running_it_twice_changes_nothing_the_second_time(self, conn):
        _run(conn, 1, "s")
        store_commits(conn, REPO_ID, [CommitRecord(sha="s", paths=["a"], tree_sha="t")])
        assert backfill_tree_shas(conn, REPO_ID) == 1
        assert backfill_tree_shas(conn, REPO_ID) == 0


class TestCoverageIsReportable:
    def test_it_reports_both_halves_of_the_fraction(self, conn):
        _run(conn, 1, "a")
        _run(conn, 2, "b")
        _run(conn, 3, "c")
        store_commits(conn, REPO_ID, [CommitRecord(sha="a", paths=["x"])])
        assert coverage(conn, REPO_ID) == (1, 3)

    def test_full_coverage_reads_as_equal(self, conn):
        _run(conn, 1, "a")
        store_commits(conn, REPO_ID, [CommitRecord(sha="a", paths=["x"])])
        fetched, seen = coverage(conn, REPO_ID)
        assert fetched == seen == 1

    def test_a_repo_with_no_runs_reports_zero_not_an_error(self, conn):
        assert coverage(conn, REPO_ID) == (0, 0)
