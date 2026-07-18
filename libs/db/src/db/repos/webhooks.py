"""webhook_events repository — the ONLY external-callback durability surface.

Ingest is an idempotent INSERT deduped by delivery_guid (a duplicate delivery is
a no-op); processing drains pending rows on boot + periodically. This is the only
callback-durability table — there is no general in-Postgres event bus.
"""
from __future__ import annotations

import json
from typing import Any


async def insert_event(
    conn: Any, delivery_guid: str, payload: dict[str, Any] | None = None
) -> bool:
    """Durably record a delivery; returns True if newly inserted (else dedup)."""
    row = await conn.fetchrow(
        """
        INSERT INTO webhook_events (delivery_guid, status, payload)
        VALUES ($1, 'pending', $2::jsonb)
        ON CONFLICT (delivery_guid) DO NOTHING
        RETURNING id
        """,
        delivery_guid,
        json.dumps(payload or {}),
    )
    return row is not None


async def list_pending(conn: Any) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, delivery_guid, status, payload FROM webhook_events "
        "WHERE status = 'pending' ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def mark_processed(conn: Any, event_id: Any) -> None:
    await conn.execute(
        "UPDATE webhook_events SET status = 'processed', processed_at = now() "
        "WHERE id = $1",
        event_id,
    )
