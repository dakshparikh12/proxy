"""control_plane webhook intake — durable-insert-first, then 200, then drain.

An inbound provider webhook (GitHub/Recall) is deduped and made durable in a
single INSERT-on-conflict into ``webhook_events`` BEFORE the 200 is returned; no
processing happens on the request path. A duplicate delivery (same
``delivery_guid``) is a no-op. Pending rows are drained idempotently on boot.
There is deliberately NO general fan-out stream — ``webhook_events`` is the only
external-callback durability (the substrate has no broker).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Json


@dataclass(frozen=True)
class WebhookResponse:
    """The immediate webhook ack (returned before any processing)."""

    status: int


def ingest(
    conn: Any,
    *,
    delivery_guid: str,
    body: dict[str, Any],
    on_step: Callable[[str], None] | None = None,
) -> WebhookResponse:
    """Durably land a webhook (dedup), then return 200 — no processing yet."""
    step = on_step if on_step is not None else (lambda _s: None)
    conn.execute(
        """
        INSERT INTO webhook_events (delivery_guid, payload, status)
        VALUES (%s, %s, 'pending')
        ON CONFLICT (delivery_guid) DO NOTHING
        """,
        (delivery_guid, Json(body)),
    )
    step("inserted")
    # The durable INSERT precedes the 200; processing is deferred to drain.
    step("returned_200")
    return WebhookResponse(status=200)


def drain_pending(conn: Any) -> int:
    """Idempotently process every pending webhook row; return how many drained."""
    rows = conn.execute(
        "SELECT id FROM webhook_events WHERE status = 'pending'"
    ).fetchall()
    for (row_id,) in rows:
        conn.execute(
            "UPDATE webhook_events SET status = 'processed', processed_at = now() "
            "WHERE id = %s AND status = 'pending'",
            (row_id,),
        )
    return len(rows)
