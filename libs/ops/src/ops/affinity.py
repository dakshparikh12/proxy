"""WebSocket affinity — route a client to the owning instance (AC-OBS-007).

The owner instance-id is persisted on the claimed operation_runs row
(``created_by``); affinity reads it so a WS reconnect lands on the process that
actually owns the meeting.
"""
from __future__ import annotations

from libs.db import Database

from .claim import MEETING_HARNESS_OP


async def route_to_owner(
    db: Database, scope_id: str, operation_type: str = MEETING_HARNESS_OP
) -> str | None:
    """Return the owning instance-id for a scope, or None if unclaimed."""
    row = await db.get_operation_run(scope_id, operation_type)
    if row is None or row.get("status") != "running":
        return None
    created_by = row.get("created_by")
    return str(created_by) if created_by is not None else None
