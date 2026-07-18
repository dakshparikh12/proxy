"""Workroom task recovery — restart the coarse unit unless its deliverable exists.

A Workroom task is an operation_runs row (operation_type='workroom:<id>'), NOT a
row in a bespoke workroom_tasks table. Recovery runs a SQL completion check: if
the persisted result_ref already points at the deliverable, the task is done and
is NOT re-run; otherwise the whole coarse unit is restarted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from libs.db import Database

# A Workroom task is keyed as operation_type='workroom:<task-id>'.
WORKROOM_OP_PREFIX = "workroom:"


@dataclass(frozen=True)
class RecoverResult:
    restarted: bool


def _has_deliverable(result_ref: Any) -> bool:
    if result_ref is None:
        return False
    value = result_ref
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    return isinstance(value, dict) and bool(value.get("deliverable"))


def should_restart(conn: Any, operation_id: Any) -> bool:
    """Restart-unless-deliverable-exists (sync): False iff the deliverable exists.

    A recycled orchestrator re-runs the coarse Workroom unit unless the persisted
    ``result_ref`` already points at the deliverable — an idempotent completion
    check over the SAME operation_runs row, never a bespoke task table.
    """
    row = conn.execute(
        "SELECT result_ref FROM operation_runs WHERE id = %s", (operation_id,)
    ).fetchone()
    return not _has_deliverable(row[0] if row is not None else None)


async def recover_task(
    db: Database, scope_id: str, operation_type: str
) -> RecoverResult:
    """Re-run the task unless a SQL completion check shows the deliverable exists."""
    async with db.acquire() as conn:
        result_ref = await conn.fetchval(
            "SELECT result_ref FROM operation_runs "
            "WHERE scope_id = $1 AND operation_type = $2 "
            "ORDER BY started_at DESC LIMIT 1",
            scope_id,
            operation_type,
        )
    return RecoverResult(restarted=not _has_deliverable(result_ref))
