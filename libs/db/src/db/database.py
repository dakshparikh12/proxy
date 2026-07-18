"""The asyncpg-backed ``Database`` facade — the single durable-state handle.

Owns one connection pool over the one Cloud SQL Postgres. Every caller borrows a
connection via :meth:`acquire`; the repository functions in ``db.repos`` carry
the parameterised SQL. The two operation-run reaper paths (lazy-on-read and the
boot bulk sweep) live here because they are the substrate's read barrier.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import asyncpg

from .config import stale_after_s

if TYPE_CHECKING:  # pragma: no cover
    from .sync import _SyncDatabase


def _normalise_dsn(dsn: str) -> str:
    # asyncpg speaks the bare libpq URL; strip any SQLAlchemy driver suffix.
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


async def open_pool(dsn: str) -> Any:
    """The ONE asyncpg pool-construction site (§11 canonical config).

    In prod the DSN is a Cloud SQL Auth Proxy Unix socket (``host=/cloudsql/
    <project>:<region>:<instance>``) — the proxy terminates TLS, so the DSN
    carries NO SSL params. ~2 Cloud Run instances × max_size 20 ≈ 40 connections,
    under the Cloud SQL limit.
    """
    return await asyncpg.create_pool(
        _normalise_dsn(dsn),
        min_size=2,
        max_size=20,
        max_inactive_connection_lifetime=30,
        command_timeout=10,
    )


class Database:
    """A pooled handle to the durable Postgres substrate."""

    def __init__(self, pool: Any, instance_id: str) -> None:
        self._pool = pool
        self._instance_id = instance_id
        # Per-scope sandbox keepalive markers (process-local; the heartbeat loop
        # refreshes these during silent agent work — see :meth:`bump_activity`).
        self._sandbox_activity: dict[str, float] = {}

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
        pool = await open_pool(dsn)
        return cls(pool, instance_id or f"proc-{uuid.uuid4().hex}")

    @classmethod
    def from_connection(cls, conn: Any) -> _SyncDatabase:
        """Wrap one raw (autocommit) psycopg3 connection in the SYNC facade.

        The async pool path (:meth:`connect`) is untouched; this is a separate,
        connection-scoped mirror for the broker-free sync workflow. The caller
        owns the connection's lifetime — the facade never opens or closes it.
        """
        from .sync import _SyncDatabase

        return _SyncDatabase(conn)

    @property
    def repos(self) -> Any:
        """The per-domain repository namespace this facade owns (§11)."""
        from .repos.repositories import Repos

        return Repos(self)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        async with self._pool.acquire() as conn:
            yield conn

    def last_activity_at(self, scope_id: str) -> float | None:
        """Monotonic time of this scope's last keepalive bump, or None if never."""
        return self._sandbox_activity.get(scope_id)

    async def bump_activity(self, scope_id: str) -> None:
        """Keep the scope's E2B sandbox alive during silent agent work.

        The heartbeat loop calls this (through the db facade it already holds) on
        every tick — distinct from the fencing heartbeat: advancing
        ``last_heartbeat_at`` proves ownership, whereas this refreshes the
        scope's keepalive marker so its sandbox is not reaped while the
        operation_run remains 'running'. The marker lives on this substrate facade
        (a leaf) and is exposed via :meth:`last_activity_at` for the TTL reconcile
        (libs.ops, which depends on this facade) to spare a still-active scope.
        Recording it here — never in libs.ops — keeps libs.db free of an upward
        dependency.
        """
        self._sandbox_activity[scope_id] = time.monotonic()

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
