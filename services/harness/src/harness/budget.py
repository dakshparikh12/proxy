"""Budget + seam-metered cost writes at the harness boundary."""
from __future__ import annotations

from typing import Any

from libs.db import Database
from libs.ops import MeetingCost, record_micro_call_cost
from libs.ops import check_meeting_budget as _ops_check_budget


async def check_meeting_budget(db: Database, meeting_id: Any) -> MeetingCost:
    """Reload the persisted spend for a (possibly recycled) meeting."""
    return await _ops_check_budget(db, meeting_id)


async def record_seam_cost(
    db: Database, *, meeting_id: Any, model_usd: float
) -> None:
    """Seam-metered spend (wakes + Workroom) → meeting_cost.model_usd."""
    await record_micro_call_cost(db, meeting_id, model_usd)
