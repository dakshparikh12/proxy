"""Connect-trigger + push-webhook wiring — the map hooks are LIVE + additive (PM-READY/REFRESH).

The connect trigger builds the map on the already-cloned repo (additive to the graph index that
still feeds the referent seam); the webhook refreshes it on a verified push. Both no-op honestly
when the model seam is unfunded (D-032) and drive the real build/store/verify with a fake provider.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentkit import ProviderQuery
from libs.contracts import AgentChunk

from premeeting import connect_hook, map_store
from premeeting.cloner import Cloner

_DSN = (os.environ.get("PREMEETING_TEST_DSN") or os.environ.get("TEST_DATABASE_URL") or "").strip()
requires_pg = pytest.mark.skipif(not _DSN, reason="live scratch Postgres not provisioned")

_FAITHFUL = """# Repo Map — fixture-repo @ HEAD

## What this is
A service.

## Where things live
- src/ — the app
- tests/ — the tests

## Entry points
- src/server.py — server

## Key models / domain
- src/models.py — types

## Conventions
pytest.

## Notes
one lang.
"""


class FaithfulProvider:
    name = "claude"

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        yield AgentChunk(type="TEXT", text=_FAITHFUL, metadata={"msg_id": "m"})
        yield AgentChunk(type="RESULT", metadata={"num_turns": 1})


def _files() -> dict[str, str]:
    return {
        "src/server.py": "def main(): ...\n",
        "src/models.py": "class T: ...\n",
        "tests/test_x.py": "def t(): ...\n",
        "README.md": "# f\n",
    }


class _AcquireCtx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *a: Any) -> None:
        return None


class _DB:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._conn)


# ── the hook NO-OPS honestly with no provider (D-032) ───────────────────────
@pytest.mark.asyncio
async def test_connect_hook_noops_without_provider(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(_files())
    checkout = Cloner().clone("tenant-a", src.as_uri())
    out = await connect_hook.run_map_build_for_clone(
        tenant_id="tenant-a", repo="fixture-repo", clone_path=checkout,
        provider=None, map_store=None,
    )
    assert not out.built  # honest no-op, never a fabricated map
    assert out.reasons and "D-032" in out.reasons[0]


# ── the hook drives the REAL build/verify with a fake provider ──────────────
@pytest.mark.asyncio
async def test_connect_hook_builds_and_verifies_with_fake_provider(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(_files())
    checkout = Cloner().clone("tenant-a", src.as_uri())
    out = await connect_hook.run_map_build_for_clone(
        tenant_id="tenant-a", repo="fixture-repo", clone_path=checkout,
        provider=FaithfulProvider(), map_store=None,
    )
    assert out.built and out.ready, out.reasons


@requires_pg
@pytest.mark.asyncio
async def test_connect_hook_stores_map_durably(make_git_repo: Any) -> None:
    import asyncpg

    src, _sha = make_git_repo(_files())
    checkout = Cloner().clone("tenant-a", src.as_uri())
    conn = await asyncpg.connect(_DSN)
    tid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tenants (id) VALUES ($1) ON CONFLICT DO NOTHING", tid)
    try:
        store = map_store.MapStore(db=_DB(conn))
        out = await connect_hook.run_map_build_for_clone(
            tenant_id=tid, repo="fixture-repo", clone_path=checkout,
            provider=FaithfulProvider(), map_store=store,
        )
        assert out.built and out.ready
        # The map is durably readable back at the resolved SHA.
        loaded = await store.load(tenant_id=tid, repo="fixture-repo", sha=out.sha)
        assert loaded is not None and loaded.startswith("# Repo Map")
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'fixture-repo'")
        await conn.close()


# ── the connect trigger's _maybe_build_map bridge is present + guarded ──────
def test_trigger_map_hook_present_and_guarded() -> None:
    from control_plane import connect

    # The trigger accepts the injected map provider + store (the wire is present).
    import inspect

    sig = inspect.signature(connect.trigger_connect_index)
    assert "map_provider" in sig.parameters and "map_store" in sig.parameters
    # The sync→async bridge exists and no-ops safely on a pipeline with no clone.
    class _P:
        clone_path = None

    assert connect._maybe_build_map(
        tenant_id="t", repo_url="https://x/r", pipeline=_P(), map_provider=None, map_store=None
    ) is None


# ── the webhook's _maybe_refresh_map is guarded (no provider → no-op, never 500) ─────────
def test_webhook_map_refresh_noops_without_provider() -> None:
    from control_plane import github_webhook

    class _State:
        pass

    class _App:
        state = _State()

    class _WH:
        repo_url = "https://github.com/a/b"
        changed_files: list[str] = []

    # No map_provider on app.state → honest no-op, never raises.
    assert github_webhook._maybe_refresh_map(_App(), _WH()) is None
