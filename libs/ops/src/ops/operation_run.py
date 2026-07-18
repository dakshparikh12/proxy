"""with_operation_run + OperationHandle — the durable-run lifecycle & fence.

A run is a single ``operation_runs`` row. On entry the context manager atomically
claims a 'running' row (created_by = the owning instance-id) and starts a
fencing heartbeat; on exit it flips the row to 'completed' or 'failed'.

The fence: the heartbeat UPDATE is gated on ``status='running'``. While the
instance is the owner the UPDATE affects exactly one row (is_owner stays True).
If the row was reclaimed/reaped (no longer 'running') the UPDATE affects zero
rows → is_owner flips False → the zombie must self-terminate and emit nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from libs.db import Database
from libs.db import heartbeat_s as _default_heartbeat_s


class OperationHandle:
    """The live handle for an owned operation_runs row (fence + pause seam)."""

    def __init__(
        self, db: Database, run_id: Any, scope_id: str, operation_type: str
    ) -> None:
        self._db = db
        self._run_id = run_id
        self._scope_id = scope_id
        self._operation_type = operation_type
        self._is_owner = True

    @property
    def run_id(self) -> Any:
        return self._run_id

    @property
    def is_owner(self) -> bool:
        return self._is_owner

    async def heartbeat(self) -> bool:
        """Fencing heartbeat: return True iff this instance still owns the row.

        A zero-rowcount UPDATE (the row is no longer 'running' — reclaimed or
        reaped) drives is_owner False, the self-terminate signal.
        """
        async with self._db.acquire() as conn:
            status = await conn.execute(
                "UPDATE operation_runs SET last_heartbeat_at = now() "
                "WHERE id = $1 AND status = 'running'",
                self._run_id,
            )
        affected = _rowcount(status)
        self._is_owner = affected == 1
        return self._is_owner

    async def check_pause(self) -> bool:
        """Surface pause_requested so a running build can be paused/aborted."""
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                "SELECT pause_requested FROM operation_runs "
                "WHERE id = $1 AND status = 'running'",
                self._run_id,
            )
        return bool(value)


def _rowcount(command_tag: str) -> int:
    # asyncpg returns e.g. "UPDATE 1" / "UPDATE 0".
    parts = command_tag.split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0


async def _claim_running(
    db: Database, scope_id: str, operation_type: str
) -> Any | None:
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
            db.instance_id,
        )
    return row["id"] if row is not None else None


async def _finish(
    db: Database, run_id: Any, status: str, error: str | None = None
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE operation_runs "
            "SET status = $2, completed_at = now(), error = $3 "
            "WHERE id = $1 AND status = 'running'",
            run_id,
            status,
            error,
        )


async def _heartbeat_loop(handle: OperationHandle, interval_s: float) -> None:
    try:
        while True:
            await asyncio.sleep(interval_s)
            if not await handle.heartbeat():
                return  # lost the fence — stop heartbeating (self-terminate)
    except asyncio.CancelledError:
        return


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def _with_operation_run_async(
    db: Database,
    scope_id: str,
    operation_type: str,
    *,
    heartbeat_s: float | None = None,
) -> AsyncIterator[OperationHandle]:
    """Claim a running row (+ created_by owner), heartbeat it, finalize on exit."""
    interval = (
        float(heartbeat_s)
        if heartbeat_s is not None
        else float(_default_heartbeat_s())
    )
    run_id = await _claim_running(db, scope_id, operation_type)
    if run_id is None:
        raise RuntimeError(
            f"operation already owned: {scope_id!r}/{operation_type!r}"
        )
    handle = OperationHandle(db, run_id, scope_id, operation_type)
    task: asyncio.Task[None] = asyncio.create_task(
        _heartbeat_loop(handle, interval)
    )
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001 — finalize then re-raise
        await _cancel(task)
        await _finish(db, run_id, "failed", error=repr(exc))
        raise
    else:
        await _cancel(task)
        await _finish(db, run_id, "completed")


# ---------------------------------------------------------------------------
# Synchronous mirror — one raw psycopg3 connection, the broker-free workflow path
# ---------------------------------------------------------------------------


class _SyncOperationHandle:
    """Sync fence handle over one raw psycopg3 connection (mirror of ``OperationHandle``)."""

    def __init__(
        self, conn: Any, run_id: Any, scope_id: str, operation_type: str
    ) -> None:
        self._conn = conn
        self._run_id = run_id
        self._scope_id = scope_id
        self._operation_type = operation_type
        self._is_owner = True

    @property
    def run_id(self) -> Any:
        return self._run_id

    @property
    def is_owner(self) -> bool:
        return self._is_owner

    def heartbeat(self) -> bool:
        """Fencing heartbeat (sync): rowcount-0 UPDATE clears ownership."""
        cur = self._conn.execute(
            "UPDATE operation_runs SET last_heartbeat_at = now() "
            "WHERE id = %s AND status = 'running'",
            (self._run_id,),
        )
        self._is_owner = (int(getattr(cur, "rowcount", 0) or 0)) == 1
        return self._is_owner

    def check_pause(self) -> bool:
        value = self._conn.execute(
            "SELECT pause_requested FROM operation_runs "
            "WHERE id = %s AND status = 'running'",
            (self._run_id,),
        ).fetchone()
        return bool(value[0]) if value is not None else False


@contextmanager
def _with_operation_run_sync(
    conn: Any, scope_id: str, operation_type: str
) -> Iterator[_SyncOperationHandle]:
    """Claim a running row on a raw psycopg3 connection; finalize on exit."""
    cur = conn.execute(
        "INSERT INTO operation_runs (scope_id, operation_type, status, created_by) "
        "VALUES (%s, %s, 'running', %s) "
        "ON CONFLICT (scope_id, operation_type) WHERE status = 'running' "
        "DO NOTHING "
        "RETURNING id",
        (str(scope_id), operation_type, f"proc-{uuid.uuid4().hex}"),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"operation already owned: {scope_id!r}/{operation_type!r}"
        )
    run_id = row[0]
    handle = _SyncOperationHandle(conn, run_id, scope_id, operation_type)
    try:
        yield handle
    finally:
        # Only finalize if this handle still owns the row (never clobber a
        # reclaimed/interrupted row a replacement now owns).
        conn.execute(
            "UPDATE operation_runs "
            "SET status = 'completed', completed_at = now() "
            "WHERE id = %s AND status = 'running'",
            (run_id,),
        )


def with_operation_run(
    db: Any = None,
    scope_id: str = "",
    operation_type: str = "",
    *,
    op_type: str | None = None,
    heartbeat_s: float | None = None,
) -> Any:
    """Own a durable operation_runs row for the duration of a ``with`` block.

    ``Database`` first arg → the async context manager (heartbeat task + pool);
    a raw psycopg connection → the synchronous mirror (broker-free workflow path).
    ``op_type`` is accepted as an alias for ``operation_type``.
    """
    optype = op_type if op_type is not None else operation_type
    if isinstance(db, Database):
        return _with_operation_run_async(
            db, scope_id, optype, heartbeat_s=heartbeat_s
        )
    return _with_operation_run_sync(db, scope_id, optype)
