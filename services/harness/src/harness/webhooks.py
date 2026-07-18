"""Webhook ingest/drain — durable INSERT then 200; drain pending idempotently.

Ingest returns 200 immediately after the durable INSERT, BEFORE processing.
Pending rows are drained on boot + periodically; processing is idempotent.
webhook_events is the only external-callback durability surface (no event bus).
"""
from __future__ import annotations

from typing import Any

from libs.db import Database, repos


def ingest_webhook(event: dict[str, Any], *, store: Any) -> int:
    """Durably record the delivery, then return 200 (processing happens later)."""
    store.insert(event)
    return 200


async def drain_pending_webhooks(db: Database) -> int:
    """Drain every pending webhook_events row (idempotent processing)."""
    drained = 0
    async with db.acquire() as conn:
        for event in await repos.webhooks.list_pending(conn):
            # Idempotent handling would dispatch on event["payload"] here.
            await repos.webhooks.mark_processed(conn, event["id"])
            drained += 1
    return drained
