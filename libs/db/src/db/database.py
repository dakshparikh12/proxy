"""The asyncpg-backed ``Database`` facade — the single durable-state handle.

Owns one connection pool over the one Cloud SQL Postgres. Every caller borrows a
connection via :meth:`acquire`; the repository functions in ``db.repos`` carry
the parameterised SQL. The two operation-run reaper paths (lazy-on-read and the
boot bulk sweep) live here because they are the substrate's read barrier.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from .config import stale_after_s


def _normalise_dsn(dsn: str) -> str:
    # asyncpg speaks the bare libpq URL; strip any SQLAlchemy driver suffix.
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


class Database:
    """A pooled handle to the durable Postgres substrate."""

    def __init__(self, pool: Any, instance_id: str) -> None:
        self._pool = pool
        self._instance_id = instance_id

    @property
    def instance_id(self) -> str:
        """The claiming instance-id written to operation_runs.created_by."""
        return self._instance_id

    @property
    def pool(self) -> Any:
        return self._pool

    @classmethod
    async def connect(
        cls, dsn: str | None, *, instance_id: str | None = None
    ) -> Database:
        if not dsn:
            raise ValueError("Database.connect requires a DSN")
        pool = await asyncpg.create_pool(
            _normalise_dsn(dsn), min_size=1, max_size=5
        )
        return cls(pool, instance_id or f"proc-{uuid.uuid4().hex}")

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        async with self._pool.acquire() as conn:
            yield conn

    # ── operation-run reaper barrier ────────────────────────────────────────
    async def sweep_stale_operation_runs(self) -> int:
        """Boot-time bulk sweep: every stale running row → 'interrupted'.

        Idempotent — a second run over the same state is a no-op. Fresh rows
        (heartbeat within STALE_AFTER_S) are never touched.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE operation_runs
                   SET status = 'interrupted', completed_at = now()
                 WHERE status = 'running'
                   AND last_heartbeat_at < now() - make_interval(secs => $1)
                RETURNING id
                """,
                float(stale_after_s()),
            )
        return len(rows)

    async def get_operation_run(
        self, scope_id: str, operation_type: str
    ) -> dict[str, Any] | None:
        """Reaper-on-read: sweep stale rows, then return the requested row."""
        await self.sweep_stale_operation_runs()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, scope_id, operation_type, status, progress,
                       result_ref, error, pause_requested, created_by,
                       started_at, completed_at, last_heartbeat_at
                  FROM operation_runs
                 WHERE scope_id = $1 AND operation_type = $2
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                scope_id,
                operation_type,
            )
        return dict(row) if row is not None else None
