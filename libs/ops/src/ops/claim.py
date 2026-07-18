"""Broker-free cross-process coordination — Postgres is the ONLY arbiter.

``claim_meeting`` is an atomic INSERT ... ON CONFLICT DO NOTHING against the
partial unique index (one 'running' row per (scope_id, operation_type)): the
non-null returner owns the meeting; a concurrent duplicate join gets NULL and
backs off. ``with_meeting_lock`` serialises a per-meeting critical section on a
transaction-scoped advisory lock. There is no in-memory lock and no message
broker anywhere in this path.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from libs.db import Database

MEETING_HARNESS_OP = "meeting-harness"


async def claim_meeting(
    db: Database,
    scope_id: str,
    operation_type: str = MEETING_HARNESS_OP,
    *,
    created_by: str | None = None,
) -> Any | None:
    """Atomic claim: return the new row id if we won, else None (loser backs off)."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO operation_runs (scope_id, operation_type, status, created_by)
            VALUES ($1, $2, 'running', $3)
            ON CONFLICT (scope_id, operation_type) WHERE status = 'running'
            DO NOTHING
            RETURNING id
            """,
            scope_id,
            operation_type,
            created_by or db.instance_id,
        )
    return row["id"] if row is not None else None


@asynccontextmanager
async def with_meeting_lock(db: Database, meeting_key: str) -> AsyncIterator[Any]:
    """Hold pg_advisory_xact_lock(hashtext(key), 0) for a per-meeting section."""
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), 0)", meeting_key
            )
            yield conn
