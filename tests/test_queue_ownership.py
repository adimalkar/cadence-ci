"""Real queue ownership across committed claims and separate worker connections."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence import config, queue, worker
from cadence.db import apply_migrations
from cadence.providers.base import AccessDenied, Expired, NotFound, RateLimited

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def item():
    marker = str(uuid4())
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        job_id = queue.enqueue(
            conn, "poll_repo", {"claim_test": marker}, run_at=datetime(1900, 1, 1, tzinfo=UTC)
        )
        conn.commit()
        try:
            yield conn, job_id
        finally:
            conn.rollback()
            conn.execute("DELETE FROM ingest_job WHERE payload->>'claim_test'=%s", (marker,))
            conn.commit()


def claim(conn, job_id):
    value = queue.claim_next(conn)
    assert value is not None and value["id"] == job_id
    assert isinstance(value["claim_token"], UUID)
    conn.commit()
    return value


def expire(conn, job_id):
    conn.execute(
        "UPDATE ingest_job SET updated_at=now()-%s WHERE id=%s",
        (queue.LEASE + timedelta(seconds=1), job_id),
    )
    conn.commit()


def row(conn, job_id):
    return conn.execute("SELECT * FROM ingest_job WHERE id=%s", (job_id,)).fetchone()


@pytest.mark.parametrize("ack", ["complete", "retry", "terminal"])
def test_reclaimed_claim_rejects_all_old_acknowledgements(item, ack):
    conn, job_id = item
    old = claim(conn, job_id)
    expire(conn, job_id)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as other:
        current = claim(other, job_id)
    assert old["claim_token"] != current["claim_token"]
    before = row(conn, job_id)
    if ack == "complete":
        accepted = queue.complete(conn, job_id, claim_token=old["claim_token"])
    else:
        accepted = queue.fail(
            conn,
            job_id,
            "late error",
            claim_token=old["claim_token"],
            retry_in=timedelta(minutes=5) if ack == "retry" else None,
        )
    conn.commit()
    assert not accepted and row(conn, job_id) == before
    assert queue.complete(conn, job_id, claim_token=current["claim_token"])
    conn.commit()
    assert row(conn, job_id)["status"] == "done"
    assert row(conn, job_id)["claim_token"] is None


def test_retry_clears_claim_and_next_claim_has_fresh_token(item):
    conn, job_id = item
    first = claim(conn, job_id)
    assert queue.fail(
        conn, job_id, "retry", retry_in=timedelta(0), claim_token=first["claim_token"]
    )
    conn.commit()
    assert row(conn, job_id)["attempts"] == 1 and row(conn, job_id)["claim_token"] is None
    assert not queue.complete(conn, job_id, claim_token=first["claim_token"])
    conn.commit()
    second = claim(conn, job_id)
    assert second["claim_token"] != first["claim_token"] and second["attempts"] == 1


@pytest.mark.parametrize("action", ["done", "failed"])
def test_terminal_ack_is_not_repeatable_or_reversible(item, action):
    conn, job_id = item
    current = claim(conn, job_id)
    token = current["claim_token"]
    if action == "done":
        assert queue.complete(conn, job_id, claim_token=token)
    else:
        assert queue.fail(conn, job_id, "x" * 3000, retry_in=None, claim_token=token)
    conn.commit()
    before = row(conn, job_id)
    assert before["claim_token"] is None and before["status"] == action
    if action == "failed":
        assert len(before["last_error"]) == 2000 and before["attempts"] == 1
    assert not queue.complete(conn, job_id, claim_token=token)
    assert not queue.fail(conn, job_id, "stale", retry_in=timedelta(0), claim_token=token)
    conn.commit()
    assert row(conn, job_id) == before


@pytest.mark.parametrize("token", [None, "not-a-uuid", 0])
def test_missing_or_invalid_owner_is_rejected_without_mutation(item, token):
    conn, job_id = item
    before = row(conn, job_id)
    with pytest.raises(ValueError, match="claim_token"):
        queue.complete(conn, job_id, claim_token=token)
    with pytest.raises(ValueError, match="claim_token"):
        queue.fail(conn, job_id, "bad", retry_in=None, claim_token=token)
    assert row(conn, job_id) == before


def test_wrong_uuid_cannot_ack_pending_or_processing_job(item):
    conn, job_id = item
    assert not queue.complete(conn, job_id, claim_token=uuid4())
    current = claim(conn, job_id)
    assert not queue.complete(conn, job_id + 1, claim_token=current["claim_token"])
    assert not queue.fail(conn, job_id, "wrong owner", retry_in=None, claim_token=uuid4())
    assert row(conn, job_id)["claim_token"] == current["claim_token"]


def test_legacy_tokenless_processing_row_gets_owner_only_after_reclaim(item):
    conn, job_id = item
    conn.execute(
        "UPDATE ingest_job SET status='processing',claim_token=NULL WHERE id=%s", (job_id,)
    )
    conn.commit()
    expire(conn, job_id)
    current = claim(conn, job_id)
    assert queue.complete(conn, job_id, claim_token=current["claim_token"])


def test_expiry_without_reclaim_does_not_revoke_current_owner(item):
    conn, job_id = item
    current = claim(conn, job_id)
    expire(conn, job_id)
    assert queue.complete(conn, job_id, claim_token=current["claim_token"])


def test_uncommitted_reclaim_rollback_restores_original_token(item):
    conn, job_id = item
    old = claim(conn, job_id)
    expire(conn, job_id)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as other:
        temporary = queue.claim_next(other)
        assert temporary["id"] == job_id and temporary["claim_token"] != old["claim_token"]
        other.rollback()
    assert row(conn, job_id)["claim_token"] == old["claim_token"]
    assert queue.complete(conn, job_id, claim_token=old["claim_token"])


@pytest.mark.parametrize("error", [None, ValueError("failed after reclaim")])
async def test_worker_late_ack_preserves_new_owner_and_logs_loss(item, monkeypatch, error):
    conn, job_id = item
    warnings = []
    monkeypatch.setattr(
        worker,
        "log",
        SimpleNamespace(
            warning=lambda event, **fields: warnings.append((event, fields)),
        ),
    )
    monkeypatch.setattr(config.settings, "database_url", TEST_DB)
    original_claim = queue.claim_next
    seen = False

    def claim_once(db):
        nonlocal seen
        if seen:
            return None
        seen = True
        value = original_claim(db)
        assert value["id"] == job_id
        return value

    async def dispatch(*args):
        expire(conn, job_id)
        with psycopg.connect(TEST_DB, row_factory=dict_row) as other:
            reclaimed = original_claim(other)
            assert reclaimed["id"] == job_id
            other.commit()
        if error:
            raise error

    monkeypatch.setattr(queue, "claim_next", claim_once)
    monkeypatch.setattr(worker, "_dispatch", dispatch)
    await worker.run_worker(object(), object(), concurrency=1, max_idle_iterations=1)
    saved = row(conn, job_id)
    assert saved["status"] == "processing" and saved["attempts"] == 0
    assert saved["last_error"] is None
    assert (
        "worker.claim_lost",
        {"kind": "poll_repo", "id": job_id, "action": "fail" if error else "complete"},
    ) in warnings


@pytest.mark.parametrize(
    "error,expected,failures",
    [
        (None, "done", 0),
        (AccessDenied("denied"), "failed", 0),
        (NotFound("missing"), "failed", 0),
        (Expired("expired"), "failed", 0),
        (RateLimited(30), "pending", 0),
        (TimeoutError("late"), "pending", 0),
        (ValueError("transient"), "pending", 0),
        (RateLimited(30), "failed", worker.MAX_ATTEMPTS),
        (TimeoutError("late"), "failed", worker.MAX_ATTEMPTS),
        (ValueError("transient"), "failed", worker.MAX_ATTEMPTS),
    ],
)
async def test_worker_current_owner_ack_paths_preserve_policy(
    item,
    monkeypatch,
    error,
    expected,
    failures,
):
    conn, job_id = item
    conn.execute("UPDATE ingest_job SET attempts=%s WHERE id=%s", (failures, job_id))
    conn.commit()
    monkeypatch.setattr(config.settings, "database_url", TEST_DB)
    original_claim = queue.claim_next
    seen = False

    def claim_once(db):
        nonlocal seen
        if seen:
            return None
        seen = True
        value = original_claim(db)
        assert value["id"] == job_id
        return value

    async def dispatch(*args):
        if error:
            raise error

    monkeypatch.setattr(queue, "claim_next", claim_once)
    monkeypatch.setattr(worker, "_dispatch", dispatch)
    await worker.run_worker(object(), object(), concurrency=1, max_idle_iterations=1)
    saved = row(conn, job_id)
    assert saved["status"] == expected and saved["claim_token"] is None
    assert saved["attempts"] == failures + (1 if error else 0)
    if isinstance(error, RateLimited) and expected == "pending":
        assert saved["run_at"] > saved["updated_at"] + timedelta(seconds=29)


def test_another_jobs_owner_token_cannot_change_current_claim(item):
    conn, job_id = item
    first = claim(conn, job_id)
    second_id = queue.enqueue(
        conn, "poll_repo", first["payload"], run_at=datetime(1900, 1, 2, tzinfo=UTC)
    )
    conn.commit()
    second = claim(conn, second_id)
    assert first["claim_token"] != second["claim_token"]
    before = row(conn, job_id)
    assert not queue.fail(
        conn, job_id, "wrong claim", retry_in=None, claim_token=second["claim_token"]
    )
    assert not queue.complete(conn, second_id, claim_token=first["claim_token"])
    conn.commit()
    assert row(conn, job_id) == before
    assert row(conn, second_id)["status"] == "processing"
