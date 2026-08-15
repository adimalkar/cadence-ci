"""Webhook receiver tests, run against a real Postgres and the real ASGI app via
in-process HTTP (no network, no deployed endpoint).

The Phase 0 ship criterion is "1,000 webhook deliveries -> zero drops, zero duplicates,
verified by replay + diff." GitHub's own redelivery UI needs a public HTTPS endpoint and
a registered App, neither of which exists in this environment. What's verified here is
the mechanism the criterion actually cares about: that redelivering the same delivery ID
never double-enqueues, and that a verified delivery is never lost between "received" and
"queued." That's a real, rigorous local substitute -- not a claim of having exercised
GitHub's live infrastructure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from cadence import config
from cadence.db.conn import apply_migrations
from cadence.webhook import app

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
SECRET = "test-webhook-secret"


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


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    # webhook.py does `from cadence.config import settings` -- same object identity as
    # `config.settings`, so patching attributes here is visible wherever it's read.
    monkeypatch.setattr(config.settings, "webhook_secret", SECRET)
    monkeypatch.setattr(config.settings, "database_url", TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB) as c:
        yield c
        c.execute("DELETE FROM ingest_job")
        c.execute("DELETE FROM webhook_delivery")
        c.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client, event: str, payload: dict, delivery_id: str | None = None, *, signature=...):
    body = json.dumps(payload).encode()
    sig = _sign(body) if signature is ... else signature
    headers = {"X-GitHub-Delivery": delivery_id or str(uuid.uuid4()), "X-GitHub-Event": event}
    if sig is not None:
        headers["X-Hub-Signature-256"] = sig
    return client.post("/webhooks/github", content=body, headers=headers)


WORKFLOW_JOB_PAYLOAD = {
    "action": "completed",
    "repository": {"id": 999, "owner": {"login": "acme"}, "name": "widget", "private": False},
    "workflow_job": {
        "id": 1,
        "run_id": 100,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
        "labels": ["ubuntu-latest"],
        "run_attempt": 1,
        "created_at": "2026-08-01T10:00:00Z",
        "started_at": "2026-08-01T10:00:05Z",
        "completed_at": "2026-08-01T10:01:00Z",
        "steps": [],
    },
}


class TestSignatureVerification:
    def test_valid_signature_accepted(self, client, conn):
        resp = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD)
        assert resp.status_code == 200

    def test_missing_signature_rejected(self, client, conn):
        resp = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD, signature=None)
        assert resp.status_code == 401

    def test_wrong_secret_rejected(self, client, conn):
        body = json.dumps(WORKFLOW_JOB_PAYLOAD).encode()
        bad_sig = _sign(body, secret="wrong-secret")
        resp = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD, signature=bad_sig)
        assert resp.status_code == 401

    def test_tampered_body_rejected(self, client, conn):
        # Signature computed over the original body; a byte flipped afterward must
        # fail even though the header looks well-formed.
        original = json.dumps(WORKFLOW_JOB_PAYLOAD).encode()
        sig = _sign(original)
        tampered = original.replace(b"success", b"failure")
        resp = client.post(
            "/webhooks/github",
            content=tampered,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-GitHub-Event": "workflow_job",
            },
        )
        assert resp.status_code == 401

    def test_missing_delivery_id_rejected(self, client, conn):
        body = json.dumps(WORKFLOW_JOB_PAYLOAD).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "workflow_job"},
        )
        assert resp.status_code == 400


class TestIdempotency:
    def test_duplicate_delivery_id_enqueues_once(self, client, conn):
        delivery_id = str(uuid.uuid4())
        r1 = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD, delivery_id)
        r2 = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD, delivery_id)  # redelivery
        assert r1.status_code == 200
        assert r2.status_code == 200

        deliveries = conn.execute(
            "SELECT count(*) FROM webhook_delivery WHERE delivery_id = %s", (delivery_id,)
        ).fetchone()[0]
        jobs = conn.execute(
            "SELECT count(*) FROM ingest_job WHERE payload->>'delivery_id' = %s", (delivery_id,)
        ).fetchone()[0]
        assert deliveries == 1
        assert jobs == 1  # redelivery must not double-enqueue

    def test_a_verified_delivery_is_never_enqueued_without_being_recorded(self, client, conn):
        # The atomicity property: every row in webhook_delivery for a NEW (non-dup)
        # delivery must have a matching ingest_job. A mismatch here would mean a
        # delivery was marked "received" but silently dropped before queueing.
        for _ in range(20):
            _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD)
        delivered = conn.execute("SELECT count(*) FROM webhook_delivery").fetchone()[0]
        queued = conn.execute(
            "SELECT count(*) FROM ingest_job WHERE kind = 'webhook_event'"
        ).fetchone()[0]
        assert delivered == 20
        assert queued == 20


class TestReplayAtScale:
    """The Phase 0 ship criterion: 1,000 deliveries, ~30% of them GitHub-style
    redeliveries of the same delivery ID interleaved among fresh ones, must produce
    zero drops and zero duplicates."""

    def test_1000_deliveries_zero_drops_zero_duplicates(self, client, conn):
        unique_ids = [str(uuid.uuid4()) for _ in range(700)]
        redeliveries = random.Random(42).choices(unique_ids, k=300)
        sequence = unique_ids + redeliveries
        random.Random(7).shuffle(sequence)
        assert len(sequence) == 1000

        for delivery_id in sequence:
            resp = _post(client, "workflow_job", WORKFLOW_JOB_PAYLOAD, delivery_id)
            assert resp.status_code == 200

        delivery_count = conn.execute(
            "SELECT count(DISTINCT delivery_id) FROM webhook_delivery"
        ).fetchone()[0]
        job_count = conn.execute("SELECT count(*) FROM ingest_job").fetchone()[0]

        assert delivery_count == len(unique_ids)  # zero drops
        assert job_count == len(unique_ids)  # zero duplicates
