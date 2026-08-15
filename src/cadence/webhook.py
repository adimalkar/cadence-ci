"""The webhook receiver.

Two structural rules, both non-negotiable:

  * respond 200 in under 500ms and do everything else async -- GitHub disables
    endpoints that respond slowly, so this handler does exactly two inserts and
    returns; a worker processes the event later.
  * a delivery marked "received" that was never enqueued is a silent, permanent drop.
    So the delivery record and the queued job are written in the same transaction:
    either both land or neither does.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response

from cadence.config import settings
from cadence.db import connect
from cadence.queue import enqueue

app = FastAPI(title="cadence-webhooks")


def _verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # constant-time: a length- or timing-based leak here would let an attacker forge
    # a valid signature one byte at a time.
    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
) -> Response:
    body = await request.body()

    if not settings.webhook_secret:
        raise HTTPException(500, "webhook secret not configured")
    if not _verify_signature(body, x_hub_signature_256, settings.webhook_secret):
        raise HTTPException(401, "bad signature")
    if not x_github_delivery or not x_github_event:
        raise HTTPException(400, "missing delivery headers")

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid json") from None

    # psycopg's sync driver would block the event loop here, serializing every delivery
    # against the sub-500ms budget this module exists to protect. Hand it to a thread.
    await asyncio.to_thread(_record_delivery, x_github_delivery, x_github_event, payload)
    return Response(status_code=200)


def _record_delivery(delivery_id: str, event: str, payload: dict) -> None:
    """Record the delivery and queue its work in one transaction.

    Atomicity is the point: a delivery marked received but never enqueued is a silent,
    permanent drop, and no later retry would re-send it because the ID is already known.
    """
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO webhook_delivery (delivery_id, event) VALUES (%s, %s)"
            " ON CONFLICT (delivery_id) DO NOTHING RETURNING delivery_id",
            (delivery_id, event),
        ).fetchone()

        if row is None:
            # Already seen: GitHub redelivering after a timeout, or our own retry.
            # Doing nothing is correct -- this is the guarantee the "zero duplicates"
            # ship criterion actually tests.
            conn.commit()
            return

        enqueue(
            conn,
            "webhook_event",
            {"event": event, "delivery_id": delivery_id, "body": payload},
        )
        conn.commit()
