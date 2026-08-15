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

    with connect() as conn:
        row = conn.execute(
            "INSERT INTO webhook_delivery (delivery_id, event) VALUES (%s, %s)"
            " ON CONFLICT (delivery_id) DO NOTHING RETURNING delivery_id",
            (x_github_delivery, x_github_event),
        ).fetchone()

        if row is None:
            # Already seen: GitHub redelivering after a timeout, or our own retry.
            # 200 with no reprocessing is correct either way -- this is the guarantee
            # the "zero duplicates" ship criterion is actually testing.
            conn.commit()
            return Response(status_code=200)

        enqueue(
            conn,
            "webhook_event",
            {"event": x_github_event, "delivery_id": x_github_delivery, "body": payload},
        )
        conn.commit()

    return Response(status_code=200)
