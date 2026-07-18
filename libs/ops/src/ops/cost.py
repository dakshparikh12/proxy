"""Meeting cost accounting — one persisted meeting_cost row, additive writes.

Both writers (the Scribe's bare Messages calls and the seam-based meter for wakes
+ Workroom) increment the same row. Budget reads RELOAD the persisted spend, so a
recycled orchestrator resumes from the accrued total and never resets to 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos

from .claim import claim_meeting


@dataclass(frozen=True)
class MeetingCost:
    """A meeting's reloaded spend snapshot (USD)."""

    meeting_id: Any
    model_usd: float
    cache_read_usd: float
    cache_creation_usd: float
    transport_usd: float
    e2b_usd: float

    @property
    def spent_usd(self) -> float:
        return (
            self.model_usd
            + self.cache_read_usd
            + self.cache_creation_usd
            + self.transport_usd
            + self.e2b_usd
        )


async def record_model_cost(
    db: Database,
    meeting_id: Any,
    *,
    model_usd: float = 0.0,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
) -> None:
    """Increment model spend (+ the prompt-cache read/creation split)."""
    async with db.acquire() as conn:
        await repos.cost.record_cost(
            conn,
            meeting_id,
            model_usd=model_usd,
            cache_read_usd=cache_read_usd,
            cache_creation_usd=cache_creation_usd,
        )


async def record_micro_call_cost(
    db: Database, meeting_id: Any, model_usd: float
) -> None:
    """Seam-metered micro-call spend (wakes + Workroom) → model_usd."""
    await record_model_cost(db, meeting_id, model_usd=model_usd)


async def check_meeting_budget(db: Database, meeting_id: Any) -> MeetingCost:
    """Reload the persisted spend — never resets to 0 at the recovery moment."""
    async with db.acquire() as conn:
        row = await repos.cost.get_cost(conn, meeting_id)
    if row is None:
        return MeetingCost(meeting_id, 0.0, 0.0, 0.0, 0.0, 0.0)
    return MeetingCost(
        meeting_id=meeting_id,
        model_usd=float(row["model_usd"]),
        cache_read_usd=float(row["cache_read_usd"]),
        cache_creation_usd=float(row["cache_creation_usd"]),
        transport_usd=float(row["transport_usd"]),
        e2b_usd=float(row["e2b_usd"]),
    )


async def dispatch_workroom(
    db: Database, meeting_id: str, task_id: str
) -> Any | None:
    """Dispatch a Workroom task as an operation_runs row: 'workroom:<id>'."""
    return await claim_meeting(db, meeting_id, f"workroom:{task_id}")
