"""map_store.py — durable, tenant-isolated map store (PM-STORE-01/02/03).

Real Postgres (the scratch DB migrated to head). Skips cleanly when no scratch DSN is set so
the offline static tier never depends on a live DB; the real-infra run provides
``PREMEETING_TEST_DSN`` (or ``TEST_DATABASE_URL``).
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from premeeting import map_store

_DSN = (
    os.environ.get("PREMEETING_TEST_DSN")
    or os.environ.get("TEST_DATABASE_URL")
    or ""
).strip()

requires_pg = pytest.mark.skipif(
    not _DSN, reason="live scratch Postgres not provisioned (set PREMEETING_TEST_DSN)"
)


class _AcquireCtx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *a: Any) -> None:
        return None


class _DB:
    """Minimal async Database facade over one asyncpg connection (matches libs.db.acquire)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._conn)


async def _mk_tenant(conn: Any) -> str:
    tid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tenants (id) VALUES ($1) ON CONFLICT DO NOTHING", tid)
    return tid


@requires_pg
@pytest.mark.asyncio
async def test_pm_store_01_byte_exact_round_trip() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        db = _DB(conn)
        store = map_store.MapStore(db=db)
        text = "# Repo Map — widget @ abc123\n## What this is\nA thing.\n"
        await store.save(tenant_id=tid, repo="widget", sha="abc123", map_text=text)
        got = await store.load(tenant_id=tid, repo="widget", sha="abc123")
        assert got == text  # byte-exact
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'widget'")
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_pm_store_02_tenant_isolation_no_cross_tenant_read() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        a = await _mk_tenant(conn)
        b = await _mk_tenant(conn)
        db = _DB(conn)
        store = map_store.MapStore(db=db)
        # SAME repo + sha, different tenants.
        await store.save(tenant_id=a, repo="shared", sha="s1", map_text="A-map")
        await store.save(tenant_id=b, repo="shared", sha="s1", map_text="B-map")
        # Tenant B's load NEVER returns tenant A's map, even at the same repo/sha.
        assert await store.load(tenant_id=b, repo="shared", sha="s1") == "B-map"
        assert await store.load(tenant_id=a, repo="shared", sha="s1") == "A-map"
        # A tenant with no map for that key reads None (fail-closed), never a sibling's.
        c = await _mk_tenant(conn)
        assert await store.load(tenant_id=c, repo="shared", sha="s1") is None
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'shared'")
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_pm_store_03_durable_across_fresh_store_objects() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        # Write with one store object...
        await map_store.MapStore(db=_DB(conn)).save(
            tenant_id=tid, repo="dur", sha="d1", map_text="durable-map"
        )
        # ...read with a FRESH store object over a FRESH connection (simulates another instance).
        conn2 = await asyncpg.connect(_DSN)
        try:
            got = await map_store.MapStore(db=_DB(conn2)).load(tenant_id=tid, repo="dur", sha="d1")
            assert got == "durable-map"
        finally:
            await conn2.close()
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'dur'")
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_load_latest_returns_freshest_map() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        store = map_store.MapStore(db=_DB(conn))
        await store.save(tenant_id=tid, repo="lat", sha="old", map_text="old-map")
        await store.save(tenant_id=tid, repo="lat", sha="new", map_text="new-map")
        latest = await store.load_latest(tenant_id=tid, repo="lat")
        assert latest is not None
        sha, text = latest
        assert sha == "new" and text == "new-map"
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'lat'")
        await conn.close()
