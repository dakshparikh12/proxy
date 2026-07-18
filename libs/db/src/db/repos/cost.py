"""meeting_cost repository — the single persisted spend row per meeting.

Every writer (Scribe bare-Messages calls, the seam meter for wakes + Workroom)
converges on ONE row via an upsert that *increments* — a recycled orchestrator
reloads the accrued spend, it is never reset to 0.
"""
from __future__ import annotations

from typing import Any


async def record_cost(
    conn: Any,
    meeting_id: Any,
    *,
    model_usd: float = 0.0,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
    transport_usd: float = 0.0,
    e2b_usd: float = 0.0,
) -> None:
    """Additively upsert a meeting's spend (never a destructive overwrite)."""
    await conn.execute(
        """
        INSERT INTO meeting_cost (
            meeting_id, model_usd, cache_read_usd, cache_creation_usd,
            transport_usd, e2b_usd
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (meeting_id) DO UPDATE SET
            model_usd = meeting_cost.model_usd + EXCLUDED.model_usd,
            cache_read_usd = meeting_cost.cache_read_usd + EXCLUDED.cache_read_usd,
            cache_creation_usd =
                meeting_cost.cache_creation_usd + EXCLUDED.cache_creation_usd,
            transport_usd = meeting_cost.transport_usd + EXCLUDED.transport_usd,
            e2b_usd = meeting_cost.e2b_usd + EXCLUDED.e2b_usd,
            updated_at = now()
        """,
        meeting_id,
        model_usd,
        cache_read_usd,
        cache_creation_usd,
        transport_usd,
        e2b_usd,
    )


async def get_cost(conn: Any, meeting_id: Any) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT meeting_id, model_usd, cache_read_usd, cache_creation_usd,
               transport_usd, e2b_usd, started_at, updated_at
          FROM meeting_cost
         WHERE meeting_id = $1
        """,
        meeting_id,
    )
    return dict(row) if row is not None else None
