"""Schema tests, run against a real Postgres.

The rule under test is the one the whole product rests on: a finding cannot exist without
evidence. It is enforced by a deferred constraint trigger rather than by convention,
because code review will not hold that line for 24 weeks.

Skipped when CADENCE_TEST_DATABASE_URL is unset, so the unit suite stays hermetic.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from cadence.db.conn import apply_migrations

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no test database")


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB) as c:
        yield c
        c.rollback()


def _seed_repo(conn, repo_id: int = 1) -> int:
    conn.execute(
        "INSERT INTO repo (id, owner, name) VALUES (%s, 'acme', %s)"
        " ON CONFLICT (id) DO NOTHING",
        (repo_id, f"widget{repo_id}"),
    )
    return repo_id


def _finding_params(repo_id: int, **over) -> dict:
    params = {
        "id": uuid.uuid4(),
        "repo_id": repo_id,
        "module": "waste",
        "kind": "no_dependency_cache",
        "severity": 3,
        "confidence": 0.95,
        "dedupe_key": f"key-{uuid.uuid4()}",
        "first_seen_commit": "abc123",
        "last_seen_commit": "abc123",
        "title": "npm ci runs cold on every job",
        "detector_version": "cache.node@1",
    }
    params.update(over)
    return params


_INSERT_FINDING = """
INSERT INTO finding (id, repo_id, module, kind, severity, confidence, dedupe_key,
                     first_seen_commit, last_seen_commit, title, detector_version)
VALUES (%(id)s, %(repo_id)s, %(module)s, %(kind)s, %(severity)s, %(confidence)s,
        %(dedupe_key)s, %(first_seen_commit)s, %(last_seen_commit)s, %(title)s,
        %(detector_version)s)
"""


class TestNoFindingWithoutEvidence:
    def test_finding_alone_is_rejected_at_commit(self, conn):
        repo_id = _seed_repo(conn)
        conn.execute(_INSERT_FINDING, _finding_params(repo_id))
        # The trigger is DEFERRABLE INITIALLY DEFERRED, so it fires at COMMIT -- which is
        # what lets a detector insert the finding and its evidence in either order.
        with pytest.raises(psycopg.errors.IntegrityError):
            conn.commit()

    def test_finding_with_evidence_commits(self, conn):
        repo_id = _seed_repo(conn, 2)
        params = _finding_params(repo_id)
        conn.execute(_INSERT_FINDING, params)
        conn.execute(
            "INSERT INTO evidence (finding_id, kind, run_ids) VALUES (%s, 'run_history', %s)",
            (params["id"], [1001, 1002, 1003]),
        )
        conn.commit()

        row = conn.execute(
            "SELECT count(*) AS n FROM evidence WHERE finding_id = %s", (params["id"],)
        ).fetchone()
        assert row[0] == 1


class TestEvidenceShape:
    """Each evidence kind must carry the fields that make it checkable. A `code_range`
    without a line number is not evidence, it is an assertion."""

    @pytest.mark.parametrize(
        "kind,cols,vals",
        [
            ("code_range", "file_path, line_start", (".github/workflows/ci.yml", 34)),
            ("run_history", "run_ids", ([1, 2, 3],)),
            ("counterfactual", "payload", ('{"basis":"replay","p50":9.1}',)),
            ("timing_series", "payload", ('{"step":"npm ci","durations":[290,288]}',)),
        ],
    )
    def test_valid_kinds_accepted(self, conn, kind, cols, vals):
        repo_id = _seed_repo(conn, 3)
        params = _finding_params(repo_id)
        conn.execute(_INSERT_FINDING, params)
        placeholders = ", ".join(["%s"] * len(vals))
        conn.execute(
            f"INSERT INTO evidence (finding_id, kind, {cols}) "
            f"VALUES (%s, %s, {placeholders})",
            (params["id"], kind, *vals),
        )
        conn.commit()

    @pytest.mark.parametrize(
        "kind,cols,vals",
        [
            ("code_range", "file_path", (".github/workflows/ci.yml",)),  # no line_start
            ("run_history", "run_ids", ([],)),  # empty run list
            ("log_span", "byte_start", (100,)),  # no log_chunk_id
            ("counterfactual", "file_path", ("x.yml",)),  # no payload
        ],
    )
    def test_incomplete_evidence_rejected(self, conn, kind, cols, vals):
        repo_id = _seed_repo(conn, 4)
        params = _finding_params(repo_id)
        conn.execute(_INSERT_FINDING, params)
        placeholders = ", ".join(["%s"] * len(vals))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO evidence (finding_id, kind, {cols}) "
                f"VALUES (%s, %s, {placeholders})",
                (params["id"], kind, *vals),
            )


class TestFindingConstraints:
    def test_confidence_must_be_a_probability(self, conn):
        repo_id = _seed_repo(conn, 5)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(_INSERT_FINDING, _finding_params(repo_id, confidence=1.5))

    def test_savings_basis_is_constrained(self, conn):
        """Replay and projection must never blend. An unconstrained free-text basis is
        how they'd quietly merge, so the vocabulary is enforced by the database."""
        repo_id = _seed_repo(conn, 6)
        params = _finding_params(repo_id)
        conn.execute(_INSERT_FINDING, params)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE finding SET savings_basis = 'vibes' WHERE id = %s", (params["id"],)
            )

    def test_dedupe_key_unique_per_repo_and_fingerprint(self, conn):
        repo_id = _seed_repo(conn, 7)
        key = f"dupe-{uuid.uuid4()}"
        p1 = _finding_params(repo_id, dedupe_key=key)
        conn.execute(_INSERT_FINDING, p1)
        conn.execute(
            "INSERT INTO evidence (finding_id, kind, run_ids) VALUES (%s, 'run_history', %s)",
            (p1["id"], [1]),
        )
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(_INSERT_FINDING, _finding_params(repo_id, dedupe_key=key))


class TestRateCard:
    """Rates moved on 2026-01-01 and may move again, so they are versioned data. A
    finding that cannot say which card produced its dollar figure is not auditable."""

    def test_2026_linux_rate_is_current(self, conn):
        row = conn.execute(
            "SELECT usd_per_minute, free_on_public FROM rate_card"
            " WHERE version = 2026 AND runner_label = 'ubuntu-latest'"
        ).fetchone()
        assert float(row[0]) == 0.006
        # Public repos get standard hosted runners free -- which is why waste has to be
        # denominated in hours for the OSS corpus, not dollars.
        assert row[1] is True

    def test_larger_runners_are_billed_on_public_repos(self, conn):
        row = conn.execute(
            "SELECT free_on_public FROM rate_card"
            " WHERE version = 2026 AND runner_label = 'ubuntu-latest-8-core'"
        ).fetchone()
        assert row[0] is False
