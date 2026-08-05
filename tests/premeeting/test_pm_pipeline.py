"""pipeline.py + readiness.py + refresh.py — the orchestrated real path (PM-READY-01/REFRESH-01).

Real git clone of a fixture repo + real Postgres map store. The map that gets stored is now the
DETERMINISTIC, GROUNDABLE symbol map (:func:`premeeting.symbol_map.build_symbol_map`), so the
pipeline needs no model at map-build time — a ``provider`` is still threaded (signature parity with
the deprecated LLM loop) but is never streamed. The pipeline stages run for real. Skips the
store-backed assertions cleanly when no scratch DSN is set.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentkit import ProviderQuery
from libs.contracts import AgentChunk

from premeeting import map_store, pipeline, readiness, refresh

_DSN = (os.environ.get("PREMEETING_TEST_DSN") or os.environ.get("TEST_DATABASE_URL") or "").strip()
requires_pg = pytest.mark.skipif(not _DSN, reason="live scratch Postgres not provisioned")

_FAITHFUL_MAP_TMPL = """# Repo Map — {repo} @ {sha}

## What this is
A small Python service.

## Where things live
- src/ — the app code
- tests/ — the tests

## Entry points
- src/server.py — the HTTP server

## Key models / domain
- src/models.py — the domain types

## Conventions
pytest + ruff.

## Notes
Single language.
"""


class FaithfulFakeProvider:
    """A fake model seam that returns a FAITHFUL map (every path real) so verify passes."""

    name = "claude"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        # Extract the repo/sha from the prompt's first line to keep the map faithful.
        first = prompt.splitlines()[0]
        map_text = _FAITHFUL_MAP_TMPL.format(repo="fixture-repo", sha="HEAD")
        _ = first
        yield AgentChunk(type="INIT", metadata={"session_id": "s"})
        yield AgentChunk(type="TOOL_USE", metadata={"name": "mcp__code_intel__batch_read", "input": {}})
        yield AgentChunk(type="TEXT", text=map_text, metadata={"msg_id": "m"})
        yield AgentChunk(type="RESULT", metadata={"num_turns": 2, "total_cost_usd": 0.0})


class RecordingListener:
    def __init__(self) -> None:
        self.states: list[str] = []

    def emit(self, state: str) -> None:
        self.states.append(state)


def _fixture_files() -> dict[str, str]:
    # Cross-referenced symbols so the ranking graph forms → a real, groundable symbol map.
    return {
        "src/server.py": (
            "class BaseCommand:\n"
            "    def invoke(self, ctx):\n"
            "        return process(ctx)\n"
            "\n"
            "def process(ctx):\n"
            "    return BaseCommand().invoke(ctx)\n"
        ),
        "src/models.py": (
            "from server import process, BaseCommand\n"
            "def helper():\n"
            "    return process(BaseCommand())\n"
        ),
        "tests/test_x.py": "def test_x(): ...\n",
        "README.md": "# fixture\n",
    }


# ── PM-READY-01: staged states + real verify verdict (store-less path) ───────
@pytest.mark.asyncio
async def test_pm_ready_01_stages_and_ready(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(_fixture_files())
    listener = RecordingListener()
    result = await pipeline.run_pipeline(
        tenant_id="tenant-a", repo_url=src.as_uri(), provider=FaithfulFakeProvider(),
        readiness_listener=listener,
    )
    # The REAL staged progression, in order, ending ready (map-build = the 'indexing' state).
    assert listener.states == ["connecting", "cloning", "indexing", "ready"]
    assert result.ready and result.status == "ready"
    # The STORED map is the deterministic, groundable symbol map — real file:line + ranked
    # signatures, NOT the deprecated prose shape.
    assert result.map_text.startswith("# Symbol map")
    assert "src/server.py:" in result.map_text  # real, groundable file:line
    assert "Entry points" in result.map_text and "Ranked signatures" in result.map_text
    assert "## What this is" not in result.map_text  # not the old prose map
    # No 'mapping' state was ever emitted.
    assert "mapping" not in listener.states
    sig = readiness.signal_from_result(result)
    assert sig.ready and sig.states == listener.states and sig.gaps == []


@pytest.mark.asyncio
async def test_pm_ready_01_map_is_groundable_never_hallucinated(make_git_repo: Any) -> None:
    """The deterministic symbol map cannot fabricate a path (unlike the deprecated LLM map): every
    file/dir it names resolves in the clone, so verify passes clean — that is the whole point of the
    swap (Law 1). The verify gate's hallucination-NAMING behavior itself is covered by
    ``test_pm_verify.py::test_pm_verify_01_fabricated_path_fails``."""
    src, _sha = make_git_repo(_fixture_files())
    result = await pipeline.run_pipeline(
        tenant_id="tenant-a", repo_url=src.as_uri(), provider=FaithfulFakeProvider()
    )
    assert result.ready, result.reasons
    sig = readiness.signal_from_result(result)
    assert sig.status == "ready" and sig.gaps == []


@pytest.mark.asyncio
async def test_pm_ready_01_store_fault_is_honest_not_ready_named(make_git_repo: Any) -> None:
    """A store fault → honest ``not_ready`` with the gap NAMED, propagated through the readiness
    signal (the gap-propagation contract, independent of map shape)."""
    src, _sha = make_git_repo(_fixture_files())

    class FailingStore:
        async def save(self, **_kw: Any) -> None:
            raise RuntimeError("db down")

    result = await pipeline.run_pipeline(
        tenant_id="tenant-a", repo_url=src.as_uri(), provider=FaithfulFakeProvider(),
        map_store=FailingStore(),
    )
    assert not result.ready
    sig = readiness.signal_from_result(result)
    assert sig.status == "not_ready"
    assert any(g.startswith("store:") for g in sig.gaps)  # the gap is NAMED (Law 2)


@pytest.mark.asyncio
async def test_pipeline_auth_failure_is_honest_not_ready(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(_fixture_files())
    from premeeting.github_auth import AuthError, InstallationTokenMinter

    class FailingSeam:
        async def __call__(self, op: Any, **kw: Any) -> Any:
            raise AuthError("token mint returned HTTP 401")

    minter = InstallationTokenMinter(app_id="1", private_key_pem=_dummy_pem(), call_external=FailingSeam())
    result = await pipeline.run_pipeline(
        tenant_id="t", repo_url=src.as_uri(), provider=FaithfulFakeProvider(),
        minter=minter, installation_id="42",
    )
    assert not result.ready
    assert any(r.startswith("auth:") for r in result.reasons)


# ── PM-STORE round-trip through the pipeline + PM-REFRESH-01 ─────────────────
@requires_pg
@pytest.mark.asyncio
async def test_pm_ready_01_stores_map_durably(make_git_repo: Any) -> None:
    import asyncpg

    src, _sha = make_git_repo(_fixture_files())
    conn = await asyncpg.connect(_DSN)
    tid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tenants (id) VALUES ($1) ON CONFLICT DO NOTHING", tid)
    try:
        store = map_store.MapStore(db=_DB(conn))
        result = await pipeline.run_pipeline(
            tenant_id=tid, repo_url=src.as_uri(), provider=FaithfulFakeProvider(), map_store=store,
        )
        assert result.ready
        # The map is durably readable back (byte-exact) at the pipeline's SHA.
        loaded = await store.load(tenant_id=tid, repo="fixture-repo", sha=result.sha)
        assert loaded == result.map_text
    finally:
        await conn.execute("DELETE FROM repo_maps WHERE repo = 'fixture-repo'")
        await conn.close()


@pytest.mark.asyncio
async def test_pm_refresh_01_delta_pull_rebuilds(make_git_repo: Any) -> None:
    import subprocess

    src, _sha = make_git_repo(_fixture_files())
    # First pass: connect index (creates the clone).
    first = await pipeline.run_pipeline(
        tenant_id="tenant-a", repo_url=src.as_uri(), provider=FaithfulFakeProvider()
    )
    assert first.ready

    # Push a new commit to the source, then refresh → delta-pull + rebuild + re-verify.
    (src / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(src), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "add new"], cwd=str(src), check=True, capture_output=True)

    res = await refresh.refresh_on_push(
        tenant_id="tenant-a", repo_url=src.as_uri(), provider=FaithfulFakeProvider(),
        changed_files=["src/new.py"],
    )
    assert res.rebuilt
    assert res.ready, res.reasons
    # The new SHA differs from the first pass (the delta was pulled).
    assert res.sha and res.sha != first.sha


@pytest.mark.asyncio
async def test_pm_refresh_01_no_clone_is_honest_not_ready(make_git_repo: Any) -> None:
    src, _sha = make_git_repo(_fixture_files())
    res = await refresh.refresh_on_push(
        tenant_id="never-connected", repo_url=src.as_uri(), provider=FaithfulFakeProvider()
    )
    assert not res.ready
    assert res.reasons and "connect first" in res.reasons[0]


# ── helpers ──────────────────────────────────────────────────────────────────
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


def _dummy_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
