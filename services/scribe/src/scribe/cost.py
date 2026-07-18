"""Scribe cost writes — the bare Messages call records model + cache split."""
from __future__ import annotations

from typing import Any

from libs.db import Database
from libs.ops import record_model_cost


async def record_scribe_cost(
    db: Database,
    *,
    meeting_id: Any,
    model_usd: float,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
) -> None:
    """Increment meeting_cost.model_usd and record the prompt-cache split."""
    await record_model_cost(
        db,
        meeting_id,
        model_usd=model_usd,
        cache_read_usd=cache_read_usd,
        cache_creation_usd=cache_creation_usd,
    )
