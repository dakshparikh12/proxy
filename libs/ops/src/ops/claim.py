"""Broker-free cross-process coordination — Postgres is the ONLY arbiter.

``claim_meeting`` is an atomic INSERT ... ON CONFLICT DO NOTHING against the
partial unique index (one 'running' row per (scope_id, operation_type)): the
non-null returner owns the meeting; a concurrent duplicate join gets NULL and
backs off. ``with_meeting_lock`` serialises a per-meeting critical section on a
transaction-scoped advisory lock. There is no in-memory lock and no message
broker anywhere in this path.

``claim_meeting`` is dual-path (mirrors ``libs.ops.cost``): a
:class:`~libs.db.Database` first arg drives the async persisted claim (returns a
coroutine to await); a raw psycopg connection drives the synchronous claim used
by the duplicate-join / reap-and-reclaim workflow. ``sweep_stale_on_read`` is the
synchronous reaper-on-read companion that flips stale running rows to
'interrupted' so a replacement can re-claim.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from libs.db import Database, stale_after_s

MEETING_HARNESS_OP = "meeting-harness"


async def _claim_meeting_async(
    db: Database,
    scope_id: str,
    operation_type: str,
    *,
    created_by: str | None = None,
) -> Any | None:
    """Atomic claim (async): return the new row id if we won, else None."""
    async with db.acquire() as conn:
        # operation_runs.scope_id is the sole text column (§11.2): a meeting claim
        # casts the meeting id to text at this one call site (§5.2).
        row = await conn.fetchrow(
            """
            WITH claim AS (SELECT $1 AS meeting_id)
            INSERT INTO operation_runs (scope_id, operation_type, status, created_by)
            SELECT meeting_id::text, $2, 'running', $3 FROM claim
            ON CONFLICT (scope_id, operation_type) WHERE status = 'running'
            DO NOTHING
            RETURNING id
            """,
            scope_id,
            operation_type,
            created_by or db.instance_id,
        )
    return row["id"] if row is not None else None


def _claim_meeting_sync(
    conn: Any,
    *,
    scope_id: str,
    operation_type: str,
    created_by: str | None,
) -> Any | None:
    """Atomic claim (sync): INSERT ON CONFLICT DO NOTHING, return the id or None.

    A concurrent duplicate join collides on the partial unique index (one running
    row per (scope_id, operation_type)) and gets NULL; a non-running row never
    blocks, so a reaped scope can be re-claimed.
    """
    cur = conn.execute(
        "INSERT INTO operation_runs (scope_id, operation_type, status, created_by) "
        "VALUES (%s, %s, 'running', %s) "
        "ON CONFLICT (scope_id, operation_type) WHERE status = 'running' "
        "DO NOTHING "
        "RETURNING id",
        (str(scope_id), operation_type, created_by),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


def claim_meeting(
    db: Any = None,
    scope_id: str = "",
    operation_type: str = MEETING_HARNESS_OP,
    *,
    created_by: str | None = None,
    instance_id: str | None = None,
) -> Any:
    """Claim a running row for ``scope_id``.

    ``Database`` first arg → a coroutine performing the async persisted claim; a
    raw psycopg connection → the synchronous claim (``created_by`` carries the
    owning ``instance_id``). Returns the new row id on a win, else ``None``.
    """
    owner = created_by or instance_id
    if isinstance(db, Database):
        return _claim_meeting_async(
            db, scope_id, operation_type, created_by=owner
        )
    return _claim_meeting_sync(
        db, scope_id=scope_id, operation_type=operation_type, created_by=owner
    )


def sweep_stale_on_read(conn: Any, *, scope_id: str | None = None) -> int:
    """Reaper-on-read: flip stale running rows to 'interrupted'; return the count.

    A running row whose ``last_heartbeat_at`` is older than the staleness
    threshold is orphaned (its owner crashed): it is swept to 'interrupted' so the
    partial unique index no longer blocks a replacement re-claim. Scoped to
    ``scope_id`` when given, else every scope.
    """
    if scope_id is not None:
        cur = conn.execute(
            "UPDATE operation_runs SET status = 'interrupted' "
            "WHERE status = 'running' AND scope_id = %s "
            "AND last_heartbeat_at < now() - make_interval(secs => %s)",
            (str(scope_id), float(stale_after_s())),
        )
    else:
        cur = conn.execute(
            "UPDATE operation_runs SET status = 'interrupted' "
            "WHERE status = 'running' "
            "AND last_heartbeat_at < now() - make_interval(secs => %s)",
            (float(stale_after_s()),),
        )
    return int(getattr(cur, "rowcount", 0) or 0)


@asynccontextmanager
async def with_meeting_lock(db: Database, meeting_key: str) -> AsyncIterator[Any]:
    """Hold pg_advisory_xact_lock(hashtext(key), 0) for a per-meeting section."""
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), 0)", meeting_key
            )
            yield conn
