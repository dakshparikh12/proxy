"""Acceptance tests for MAP-LOAD — the meeting map loader (``in_meeting.map_loader``).

The in-meeting engine takes the pre-meeting ``index.md`` map as an injected ``map_text``
param; ``load_meeting_map`` is the seam that loads it from the durable store for a meeting's
exact ``(tenant_id, repo, pinned_sha)`` via ``premeeting.map_store.load_map`` (the
map→context integration seam, SPEC §12). These tests pin:

- byte-exact round-trip for the pinned key,
- tenant-scoped: tenant B NEVER reads tenant A's map, even for the same ``(repo, sha)``,
- no stored row → ``None`` (honest no-map — the engine degrades, D-032),
- a different sha than what's stored → ``None`` (a meeting is PINNED, never "latest").

Real Postgres (the scratch DB migrated to head), the SAME mechanism as
``tests/premeeting/test_pm_map_store.py``: skips cleanly when no scratch DSN is set so the
offline static tier never depends on a live DB; the real-infra run provides
``PREMEETING_TEST_DSN`` (or ``TEST_DATABASE_URL``).
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from premeeting import map_store

from in_meeting.map_loader import load_meeting_map

_DSN = (
    os.environ.get("PREMEETING_TEST_DSN")
    or os.environ.get("TEST_DATABASE_URL")
    or ""
).strip()

requires_pg = pytest.mark.skipif(
    not _DSN, reason="live scratch Postgres not provisioned (set PREMEETING_TEST_DSN)"
)


async def _mk_tenant(conn: Any) -> str:
    tid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tenants (id) VALUES ($1) ON CONFLICT DO NOTHING", tid)
    return tid


@requires_pg
@pytest.mark.asyncio
async def test_map_load_01_round_trips_exact_bytes_for_pinned_key() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        text = "# Repo Map — widget @ sha1\n## What this is\nExact bytes, kept exact.\t\n"
        await map_store.save_map(conn, tenant_id=tid, repo="ml-rt", sha="sha1", map_text=text)
        got = await load_meeting_map(conn=conn, tenant_id=tid, repo="ml-rt", pinned_sha="sha1")
        assert got == text  # byte-exact
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'ml-rt'")
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_map_load_02_tenant_isolation_never_a_siblings_map() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        a = await _mk_tenant(conn)
        b = await _mk_tenant(conn)
        # Only tenant A has a map at this (repo, sha): tenant B's load is None (deny),
        # NEVER tenant A's map.
        await map_store.save_map(conn, tenant_id=a, repo="ml-iso", sha="s1", map_text="A-map")
        assert await load_meeting_map(conn=conn, tenant_id=b, repo="ml-iso", pinned_sha="s1") is None
        # When B stores its OWN map at the SAME (repo, sha), each tenant reads exactly theirs.
        await map_store.save_map(conn, tenant_id=b, repo="ml-iso", sha="s1", map_text="B-map")
        assert await load_meeting_map(conn=conn, tenant_id=b, repo="ml-iso", pinned_sha="s1") == "B-map"
        assert await load_meeting_map(conn=conn, tenant_id=a, repo="ml-iso", pinned_sha="s1") == "A-map"
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'ml-iso'")
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_map_load_03_no_row_is_honest_none_no_crash() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        got = await load_meeting_map(
            conn=conn, tenant_id=tid, repo="ml-unindexed", pinned_sha="deadbeef"
        )
        assert got is None  # honest no-map — the engine degrades (map_text=None)
    finally:
        await conn.close()


@requires_pg
@pytest.mark.asyncio
async def test_map_load_04_different_sha_is_none_pinned_not_latest() -> None:
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        tid = await _mk_tenant(conn)
        await map_store.save_map(
            conn, tenant_id=tid, repo="ml-pin", sha="stored-sha", map_text="stored-map"
        )
        # A map EXISTS for this (tenant, repo) — but the meeting pinned a different sha, so the
        # loader must return None, never silently substitute "latest".
        got = await load_meeting_map(conn=conn, tenant_id=tid, repo="ml-pin", pinned_sha="other-sha")
        assert got is None
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'ml-pin'")
        await conn.close()
