"""Ordered meeting-close — an operation_runs row + an explicit sandbox destroy.

A meeting close is represented as operation_type='meeting-close' (reusing
operation_runs, never a close_jobs table). The ordered close calls an explicit
sandbox destroy on meeting-end (one of the three sandbox bounds).
"""
from __future__ import annotations

from typing import Any

from libs.db import Database
from libs.ops import claim_meeting, sandbox_provider

MEETING_CLOSE_OP = "meeting-close"


async def close_meeting(
    db: Database, meeting_id: str, *, sandbox: Any = None
) -> Any | None:
    """Claim the meeting-close unit and explicitly destroy the sandbox."""
    run_id = await claim_meeting(db, meeting_id, MEETING_CLOSE_OP)
    if sandbox is not None:
        await sandbox_provider.destroy(sandbox)  # explicit destroy on meeting-end
    return run_id
