"""Budget + seam-metered cost writes at the harness boundary.

``check_meeting_budget`` is polymorphic (mirrors ``libs.ops`` / ``libs.db`` seams):
a ``Database`` first arg reloads the persisted ``meeting_cost`` row asynchronously;
a raw psycopg connection reads the persisted per-meeting spend synchronously and
returns the total as a number. A recycled orchestrator therefore RELOADS spend
from ``meeting_cost`` — it never resets to 0.
"""
from __future__ import annotations

from typing import Any

from libs.db import Database
from libs.ops import MeetingCost, record_micro_call_cost
from libs.ops import check_meeting_budget as _ops_check_budget


def _check_meeting_budget_sync(conn: Any, meeting_id: Any) -> float:
    """Sum the persisted meeting_cost meters for a meeting (reload, never reset)."""
    row = conn.execute(
        """
        SELECT coalesce(model_usd, 0)
             + coalesce(cache_read_usd, 0)
             + coalesce(cache_creation_usd, 0)
             + coalesce(transport_usd, 0)
             + coalesce(e2b_usd, 0)
          FROM meeting_cost
         WHERE meeting_id = %s
        """,
        (meeting_id,),
    ).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else 0.0


def check_meeting_budget(target: Any, meeting_id: Any = None) -> Any:
    """Reload the persisted spend for a (possibly recycled) meeting.

    ``Database`` first arg → an awaitable reloading the ``meeting_cost`` row (the
    async pool path); a raw psycopg connection → the synchronous total as a float.
    """
    if isinstance(target, Database):
        return _ops_check_budget(target, meeting_id)
    return _check_meeting_budget_sync(target, meeting_id)


async def record_seam_cost(
    db: Database, *, meeting_id: Any, model_usd: float
) -> None:
    """Seam-metered spend (wakes + Workroom) → meeting_cost.model_usd."""
    await record_micro_call_cost(db, meeting_id, model_usd)


__all__ = ["MeetingCost", "check_meeting_budget", "record_seam_cost"]
