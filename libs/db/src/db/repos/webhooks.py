"""webhook_events repository — the ONLY external-callback durability surface.

Ingest is an idempotent INSERT deduped by delivery_guid (a duplicate delivery is
a no-op); processing drains pending rows on boot + periodically. This is the only
callback-durability table — there is no general in-Postgres event bus.

The canonical schema (CANONICAL §12.10) is {id, provider('github'|'recall'),
delivery_guid UNIQUE, sha, payload NOT NULL, status(pending|processed|failed),
received_at}. Writes name ``provider`` (CHECK-constrained) and let ``received_at``
default to now(); ``provider`` is derived from the payload when not passed explicitly
(a Recall callback carries ``event``/``bot_id``; a GitHub push carries ``ref``/``after``).
"""
from __future__ import annotations

import json
from typing import Any


def _derive_provider(payload: dict[str, Any] | None) -> str:
    """Classify a delivery as 'github' or 'recall' from its payload shape.

    GitHub push/App deliveries carry ``ref``/``after``/``repository``/``installation``;
    Recall bot callbacks carry ``event``/``type``/``bot_id`` (or a nested ``data.bot_id``).
    Defaults to 'recall' when ambiguous — the Recall bot-status stream is the high-volume
    caller and the one the drain dispatches on. The value is CHECK-constrained in the DB,
    so an out-of-domain guess would be rejected at write time (fail-closed on the schema).
    """
    p = payload or {}
    if any(k in p for k in ("ref", "after", "repository", "installation", "commits")):
        return "github"
    return "recall"


def _derive_sha(payload: dict[str, Any] | None) -> str | None:
    """The push SHA for a GitHub delivery (``after``/``sha``), else None (Recall)."""
    p = payload or {}
    sha = p.get("after") or p.get("sha")
    return str(sha) if sha else None


async def insert_event(
    conn: Any,
    delivery_guid: str,
    payload: dict[str, Any] | None = None,
    *,
    provider: str | None = None,
    sha: str | None = None,
) -> bool:
    """Durably record a delivery; returns True if newly inserted (else dedup).

    ``provider`` (github|recall) and ``sha`` are derived from the payload when not
    passed explicitly. ``received_at`` defaults to now() in the schema.
    """
    body = payload or {}
    prov = provider if provider is not None else _derive_provider(body)
    push_sha = sha if sha is not None else _derive_sha(body)
    row = await conn.fetchrow(
        """
        INSERT INTO webhook_events (provider, delivery_guid, sha, status, payload)
        VALUES ($1, $2, $3, 'pending', $4::jsonb)
        ON CONFLICT (delivery_guid) DO NOTHING
        RETURNING id
        """,
        prov,
        delivery_guid,
        push_sha,
        json.dumps(body),
    )
    return row is not None


async def list_pending(conn: Any) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, provider, delivery_guid, sha, status, payload FROM webhook_events "
        "WHERE status = 'pending' ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def mark_processed(conn: Any, event_id: Any) -> None:
    await conn.execute(
        "UPDATE webhook_events SET status = 'processed', processed_at = now() "
        "WHERE id = $1",
        event_id,
    )
