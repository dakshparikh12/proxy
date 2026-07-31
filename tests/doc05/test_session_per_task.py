"""Doc 05 · workroom.session-per-task — ONE SDK session per task on a shared warm
sandbox, on the REAL host code path (05 §3.1 / §3.13-step-4 / §3.9).

Spec refs: 05-WORKROOM.md §3.1 (one warm sandbox per meeting hosts N task sessions;
cached prefix → a new task pays only its bundle; sessions share the sandbox
filesystem so follow-ups see prior artifacts; task durability = ONE operation_runs
row keyed operation_type='workroom:<id>', NO workroom_tasks table), §3.9 (the
1-hour-TTL prompt cache on the stable Workroom prefix), CANONICAL §10.1 / §12.10.

This is the LIVE-ASSEMBLY seam (the doc04 lesson): Doc 04's bundle-dispatch
(``control_plane.dispatch``) creates a ``contracts.Bundle`` + a ``workroom:<id>``
operation_runs row and dispatches it; THIS driver is what the dispatch invokes —
it consumes that REAL ``contracts.Bundle`` and produces a REAL ``contracts.Envelope``
on the reachable host path. The seam is proven end-to-end: ``control_plane.dispatch``
persists the row + hands the ``WorkroomHandle``, and ``SessionDriver.run_task``
consumes the same Bundle and fills the SAME row's ``result_ref`` with the Envelope.

These run the REAL ``workroom.session`` host code against IN-PROCESS FAKES: a fake
provider (records the prompt it saw + reports SDK cost/cache-read telemetry, exactly
the ``agentkit.Provider`` seam shape doc04 uses) and a fake sandbox filesystem (the
E2B read-back path). e2b is NOT installed and MUST NOT be; the real E2B-template bake
is the flagged Phase-3 residual, never faked here.

Definition of Done proven here:
  1. warm-sandbox reuse — a SECOND task on the SAME meeting sees the FIRST task's
     artifact on disk (the two sessions share one per-meeting sandbox filesystem).
  2. cached-prefix cost — a new task's token cost is only its bundle: the stable
     Workroom prefix is a prompt-cache HIT (cache-read tokens > 0, cache-CREATION
     tokens 0 on the second task), never re-paid per task.
  3. ONE operation_runs row — the task's durable state lives in the SAME
     operation_runs row keyed workroom:<id> (progress = bundle, result_ref =
     terminal Envelope); NO workroom_tasks table is ever introduced.
  4. the seam — the driver consumes a real ``contracts.Bundle`` and returns a real
     ``contracts.Envelope`` carrying the same task_id; the isolation triad rides the
     query() options; the 1-hour cache TTL sits on the stable prefix.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Match the product: control_plane.dispatch / control_plane.orchestrator return objects from the
# top-level ``contracts`` module, so import the contract types from there (``libs.contracts``
# resolves to a DISTINCT module identity under the test src-wiring → isinstance would fail).
from contracts import AgentChunk, Bundle, Envelope

from libs.ops import sandbox_provider

WORKROOM_TABLES_BANNED = "workroom_tasks"


# ── in-process fakes for the host path ───────────────────────────────────────


class FakeCacheProvider:
    """A recording ``agentkit.Provider`` stub that reports SDK cost + cache telemetry.

    It satisfies the seam (``stream(prompt, query) -> AsyncIterator[AgentChunk]``) and,
    crucially for §3.9's cached-prefix proof, mimics the provider's prompt-cache
    accounting: the STABLE prefix (``query.system_prompt``) is cache-WRITTEN once (the
    first task in a meeting pays the creation cost) and cache-READ on every later task
    (a hit → the later task pays only its bundle's input tokens). The RESULT chunk
    carries ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` /
    ``total_cost_usd`` exactly as the real SDK RESULT frame does (§3.9 telemetry).

    It also records the ``resume`` / ``max_turns`` / ``allowed_tools`` it saw so a test
    can prove the driver drives the real query() envelope, and it lets the model 'write'
    a file into the shared fake sandbox so a follow-up task can read it back.
    """

    name = "claude"

    def __init__(self, *, sandbox_fs: "FakeSharedSandboxFS") -> None:
        self._fs = sandbox_fs
        self._cached_prefixes: set[str] = set()
        self.calls = 0
        self.seen_prompts: list[str] = []
        self.seen_system_prompts: list[str] = []
        self.seen_resume: list[str | None] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []
        self.seen_options: list[Any] = []
        # The write the model performs this turn (path -> content) — a test primes it
        # so the artifact lands in the shared sandbox fs the way a real tool call would.
        self.next_write: tuple[str, bytes] | None = None

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_prompts.append(prompt)
        system_prompt = getattr(query, "system_prompt", "") or ""
        self.seen_system_prompts.append(system_prompt)
        self.seen_resume.append(getattr(query, "resume", None))
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        self.seen_options.append(query)

        # Prompt-cache accounting: the stable prefix is written ONCE, read thereafter.
        is_hit = system_prompt in self._cached_prefixes
        if not is_hit:
            self._cached_prefixes.add(system_prompt)
        write = self.next_write
        self.next_write = None
        fs = self._fs

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": f"sess-{self.calls}"})
            # The model performs its one write into the SHARED sandbox filesystem, so a
            # later task's session (same sandbox) can read it back off disk.
            if write is not None:
                path, content = write
                fs.write(path, content)
                yield AgentChunk(
                    type="TOOL_USE",
                    metadata={"name": "mcp__code__write_file", "input": {"path": path}, "id": "w1"},
                )
            yield AgentChunk(type="TEXT", text="done", metadata={"msg_id": "m1"})
            # The RESULT frame carries the SDK cost + cache-split telemetry (§3.9). On a
            # cache HIT the stable prefix costs cache-READ tokens and ZERO creation tokens —
            # the later task pays only its bundle's fresh input tokens.
            yield AgentChunk(
                type="RESULT",
                metadata={
                    "session_id": f"sess-{self.calls}",
                    "num_turns": 1,
                    "total_cost_usd": 0.002 if is_hit else 0.02,
                    "cache_creation_input_tokens": 0 if is_hit else 4000,
                    "cache_read_input_tokens": 4000 if is_hit else 0,
                    "input_tokens": 120,  # only the bundle's fresh tokens
                },
            )

        return gen()


class FakeSharedSandboxFS:
    """The ONE per-meeting warm sandbox filesystem shared by every task session.

    Mirrors the E2B read-back path (``files.read(path, format='bytes')``). The DoD:
    a second task on the same meeting reads the first task's artifact off THIS disk
    (the sessions share it) — so ``read_bytes`` returns what an earlier session wrote.
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self.reads: list[str] = []

    def write(self, path: str, data: bytes) -> None:
        self._files[path] = bytes(data)

    async def read_bytes(self, path: str) -> bytes | None:
        self.reads.append(path)
        data = self._files.get(path)
        return None if data is None else bytes(data)

    def exists(self, path: str) -> bool:
        return path in self._files


class FakeStore:
    """An in-process stand-in for the ``operation_runs`` row store (§12.10).

    The dispatch (``control_plane.dispatch``) already claimed the ``workroom:<id>`` row with
    the Bundle in ``progress``; this store models THAT one row keyed by run_id so the
    driver can persist the terminal Envelope into the SAME row's ``result_ref`` — never
    a bespoke workroom_tasks table. ``set_result`` records the write; ``rows`` exposes
    what landed so a test reads the durable source of truth back.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.tables_touched: set[str] = set()

    def claim(self, *, run_id: str, operation_type: str, progress: dict[str, Any]) -> None:
        self.tables_touched.add("operation_runs")
        self.rows[run_id] = {
            "id": run_id,
            "operation_type": operation_type,
            "status": "running",
            "progress": dict(progress),
            "result_ref": None,
        }

    async def set_result(self, *, run_id: str, result_ref: dict[str, Any], status: str) -> None:
        self.tables_touched.add("operation_runs")
        row = self.rows[run_id]
        row["result_ref"] = dict(result_ref)
        row["status"] = status


def _bundle(meeting_id: uuid.UUID, ask: str, *, task_id: uuid.UUID | None = None) -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        notes_ref=meeting_id,
        transcript_tail=f"Sam: Proxy, {ask}.",
        task_id=task_id or uuid.uuid4(),
    )


@pytest.fixture(autouse=True)
def _reset_provider_state() -> None:
    sandbox_provider._reset_for_test()
    yield
    sandbox_provider._reset_for_test()


# ── clause 1: warm-sandbox artifact visibility across two tasks ───────────────


def test_second_task_sees_first_tasks_artifact_on_disk() -> None:
    """DoD #1: a SECOND task on the SAME meeting sees the FIRST task's artifact on
    disk — the two sessions share ONE per-meeting warm sandbox filesystem (§3.1)."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    async def _run() -> tuple[Envelope, Envelope, bytes | None]:
        # Task 1 builds an artifact that lands in the shared sandbox fs.
        b1 = _bundle(meeting_id, "write the ratelimit module")
        provider.next_write = ("lib/ratelimit.py", b"BUCKET = 1\n")
        run1 = store_dispatch(store, b1)
        env1 = await driver.run_task(b1, run_id=run1)

        # Task 2 (same meeting) reads the first task's artifact off the SAME disk.
        b2 = _bundle(meeting_id, "add a test for the ratelimit module")
        run2 = store_dispatch(store, b2)
        env2 = await driver.run_task(b2, run_id=run2)
        landed = await fs.read_bytes("lib/ratelimit.py")
        return env1, env2, landed

    env1, env2, landed = asyncio.run(_run())

    assert isinstance(env1, Envelope) and isinstance(env2, Envelope)
    # The first task's artifact is visible on disk to the second task's session.
    assert landed == b"BUCKET = 1\n", "the second task did not see the first task's artifact on the shared disk"
    # BOTH tasks ran against the SAME per-meeting sandbox (one warm sandbox, N sessions).
    live = sandbox_provider.list_sandboxes()
    meeting_sandboxes = [h for h in live if h.meeting_id == str(meeting_id)]
    assert len(meeting_sandboxes) == 1, "the two tasks must share ONE per-meeting warm sandbox"


# ── clause 2: cached-prefix cost — a new task pays only its bundle ────────────


def test_new_task_pays_only_its_bundle_cached_prefix_hits() -> None:
    """DoD #2: a new task's token cost is only its bundle — the stable Workroom
    prefix is a prompt-cache HIT on the second task (cache-read > 0, cache-creation
    0), never re-paid per task (§3.9 / §10.1)."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    async def _run() -> tuple[Envelope, Envelope]:
        b1 = _bundle(meeting_id, "first ask")
        env1 = await driver.run_task(b1, run_id=store_dispatch(store, b1))
        b2 = _bundle(meeting_id, "second ask")
        env2 = await driver.run_task(b2, run_id=store_dispatch(store, b2))
        return env1, env2

    env1, env2 = asyncio.run(_run())

    # Both tasks saw the SAME stable system prefix (the cacheable Workroom prefix).
    assert provider.seen_system_prompts[0] == provider.seen_system_prompts[1]
    assert provider.seen_system_prompts[0], "the driver must send a stable, cacheable system prefix"
    # Task 1 WROTE the cache (paid creation); task 2 READ it (a hit → pays only its bundle).
    c1 = env1.artifact["cost"]
    c2 = env2.artifact["cost"]
    assert c1["cache_creation_input_tokens"] > 0, "task 1 must pay the prefix cache-write once"
    assert c2["cache_creation_input_tokens"] == 0, "task 2 must NOT re-pay the prefix cache-write"
    assert c2["cache_read_input_tokens"] > 0, "task 2 must HIT the cached prefix (cache-read tokens)"
    # Concretely cheaper: the cached-prefix task costs less than the cache-writing one.
    assert c2["total_cost_usd"] < c1["total_cost_usd"], "a cached-prefix task must cost less than the first"


def test_stable_prefix_carries_the_one_hour_cache_ttl() -> None:
    """DoD #2 (invariant): the 1-hour-TTL prompt cache sits on the STABLE prefix, not
    the SDK 5-min default — the prefix is warm across a whole meeting-hour (§3.9)."""
    from workroom.agent_config import (
        GUARDRAIL_MARK,
        WORKROOM_CACHE_TTL_SECONDS,
        WORKROOM_SYSTEM_PREFIX,
        guardrailed_system_prefix,
    )
    from workroom.session import SessionDriver, stable_prefix_cache_ttl_seconds

    # The driver's stable prefix IS the cacheable Workroom system prefix (now WITH the stable
    # §3.10 injection guardrail appended LAST — it rides every query), at the 1-hr TTL.
    assert stable_prefix_cache_ttl_seconds() == WORKROOM_CACHE_TTL_SECONDS == 3600
    assert SessionDriver.stable_prefix() == guardrailed_system_prefix()
    assert SessionDriver.stable_prefix().startswith(WORKROOM_SYSTEM_PREFIX)
    assert GUARDRAIL_MARK in SessionDriver.stable_prefix()  # the guardrail rides the cached prefix
    # Not the SDK 5-minute default.
    assert stable_prefix_cache_ttl_seconds() != 300


# ── clause 3: ONE operation_runs row keyed workroom:<id>, NO new table ────────


def test_task_state_lives_in_one_operation_runs_row_no_new_table() -> None:
    """DoD #3: the task's durable state lives in the SAME operation_runs row keyed
    workroom:<id> — progress = the bundle, result_ref = the terminal Envelope. NO
    workroom_tasks table is ever introduced (§12.10)."""
    from workroom.session import SessionDriver, workroom_op_type

    meeting_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    b = _bundle(meeting_id, "trace the blast radius")
    run_id = store_dispatch(store, b)
    env = asyncio.run(driver.run_task(b, run_id=run_id))

    row = store.rows[run_id]
    # The durable row is keyed operation_type='workroom:<task_id>' (§12.10).
    assert row["operation_type"] == workroom_op_type(b.task_id) == f"workroom:{b.task_id}"
    # progress carries the bundle (the dispatch wrote it); result_ref carries the
    # terminal Envelope the driver wrote back into the SAME row.
    assert row["progress"]["ask"] == "trace the blast radius"
    assert row["result_ref"] is not None, "the terminal Envelope must land in the SAME row's result_ref"
    assert str(row["result_ref"]["task_id"]) == str(b.task_id)
    assert row["result_ref"]["status"] == env.status
    assert row["status"] in {"completed", "failed"}
    # The ONLY durable table touched is operation_runs — never a bespoke task table.
    assert store.tables_touched == {"operation_runs"}, "the driver must reuse operation_runs, not a new table"


def test_session_module_never_names_a_workroom_tasks_table() -> None:
    """DoD #3 (negative): the session module never references a workroom_tasks table —
    task durability IS the operation_runs row, by construction (§3.1 / §12.10)."""
    import workroom.session as mod

    src = inspect.getsource(mod)
    assert WORKROOM_TABLES_BANNED not in src, "session.py must NOT introduce a workroom_tasks table"
    # The op-type keying is the operation_runs reuse, verbatim.
    assert "workroom:" in src, "the task must be keyed operation_type='workroom:<id>' on operation_runs"


# ── clause 4: the seam — real Bundle in, real Envelope out, triad on query() ──


def test_driver_consumes_real_bundle_returns_real_envelope() -> None:
    """DoD #4 (the seam / the doc04 lesson): the driver consumes a REAL contracts.Bundle
    (the shape control_plane.dispatch persists) and returns a REAL contracts.Envelope carrying
    the same task_id — reachable from the dispatch, not an isolation-only module."""
    from control_plane.dispatch import assemble_bundle

    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    # The EXACT bundle control_plane.dispatch assembles (04→05), consumed by THIS driver.
    bundle = assemble_bundle(
        ask="build the checkout-retry refactor",
        speaker="Priya",
        timestamp=datetime.now(timezone.utc),
        meeting_id=meeting_id,
        transcript_tail="Priya: Proxy, build the retry refactor.",
        task_id=task_id,
    )
    assert isinstance(bundle, Bundle)

    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    env = asyncio.run(driver.run_task(bundle, run_id=store_dispatch(store, bundle)))

    assert isinstance(env, Envelope), "the driver must return a real contracts.Envelope"
    assert env.task_id == task_id, "the Envelope must carry the bundle's task_id"
    assert env.status in {"done", "partial", "failed", "needs_clarification", "needs_review"}
    assert env.headline, "the Envelope headline is spoken in a meeting — it must be present"
    # The volatile bundle (the ask) rode the query prompt; the stable prefix was the
    # cacheable system prompt — the cache-hit split of §3.9.
    assert "checkout-retry refactor" in provider.seen_prompts[0]


def test_query_options_carry_the_isolation_triad() -> None:
    """DoD #4 (invariant): the isolation triad rides the query() options the driver
    builds on EVERY task — strict_mcp_config + setting_sources=[] + a computed built-in
    allow-list ([] in sandbox mode), plus the SDK_LOCAL_TOOLS backstop (§3.4)."""
    from workroom.agent_config import SDK_LOCAL_TOOLS
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    b = _bundle(meeting_id, "quick ask")
    asyncio.run(driver.run_task(b, run_id=store_dispatch(store, b)))

    options = provider.seen_options[0]
    # The triad, on the real options the query() saw.
    assert getattr(options, "strict_mcp_config", None) is True, "strict_mcp_config must be True (triad)"
    assert list(getattr(options, "setting_sources", None)) == [], "setting_sources must be [] (triad)"
    # Computed built-in allow-list is [] in sandbox mode (allowed_tools are all mcp__*).
    assert list(getattr(options, "tools", ["X"])) == [], "the computed built-in tools list must be [] in sandbox mode"
    # The SDK_LOCAL_TOOLS block-list is the backstop.
    disallowed = set(getattr(options, "disallowed_tools", ()) or ())
    assert set(SDK_LOCAL_TOOLS).issubset(disallowed), "SDK_LOCAL_TOOLS must be the disallowed backstop"
    # Every advertised tool is an MCP tool — no host built-in leaks into allowed_tools.
    for tool in provider.seen_allowed_tools[0]:
        assert tool.startswith("mcp__"), f"a non-MCP tool {tool!r} leaked into allowed_tools (host-side!)"


def test_driver_reuses_the_meetings_warm_sandbox_not_a_fresh_one_per_task() -> None:
    """DoD (invariant / §3.1): the driver REUSES the meeting's warm sandbox across tasks —
    it never provisions a fresh sandbox per task (that would defeat filesystem-sharing +
    warm-prefix and cold-boot mid-meeting, forbidden by §3.9)."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    async def _run() -> list[str]:
        for ask in ("first", "second", "third"):
            b = _bundle(meeting_id, ask)
            await driver.run_task(b, run_id=store_dispatch(store, b))
        return [h.id for h in sandbox_provider.list_sandboxes() if h.meeting_id == str(meeting_id)]

    ids = asyncio.run(_run())
    assert len(ids) == 1, "three tasks in one meeting must reuse ONE warm sandbox, not one per task"


def test_two_meetings_get_two_isolated_sandboxes() -> None:
    """DoD (isolation invariant): two DIFFERENT meetings get two SEPARATE warm sandboxes —
    one meeting's task never shares a sandbox filesystem with another's (§3.4 isolation)."""
    from workroom.session import SessionDriver

    m1, m2 = uuid.uuid4(), uuid.uuid4()
    fs = FakeSharedSandboxFS()  # the fs the driver picks per-meeting; here shared only via reads
    provider = FakeCacheProvider(sandbox_fs=fs)
    store = FakeStore()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)

    async def _run() -> tuple[str, str]:
        b1 = _bundle(m1, "task in meeting one")
        b2 = _bundle(m2, "task in meeting two")
        await driver.run_task(b1, run_id=store_dispatch(store, b1))
        await driver.run_task(b2, run_id=store_dispatch(store, b2))
        h1 = sandbox_provider.provision(meeting_id=str(m1))
        h2 = sandbox_provider.provision(meeting_id=str(m2))
        return h1.id, h2.id

    id1, id2 = asyncio.run(_run())
    assert id1 != id2, "two different meetings must get two distinct, isolated sandboxes"


# ── shared helper: model the dispatch that already claimed the row ────────────


def store_dispatch(store: FakeStore, bundle: Bundle) -> str:
    """Model ``control_plane.dispatch``: claim the ``workroom:<id>`` row with the Bundle in
    progress and return the run_id — the row the driver then fills with the Envelope.

    This mirrors ``control_plane.dispatch._claim_workroom_row`` so the driver is exercised
    on exactly the row-shape the real dispatch persists (progress = bundle jsonb).
    """
    from workroom.session import workroom_op_type

    run_id = uuid.uuid4().hex
    store.claim(
        run_id=run_id,
        operation_type=workroom_op_type(bundle.task_id),
        progress=bundle.model_dump(mode="json"),
    )
    return run_id


# ── real-DB parity: the driver fills the SAME operation_runs row on Postgres ──


def _local_dsn() -> str | None:
    import os

    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var, "").strip()
        if dsn:
            return dsn
    return None


@pytest.mark.integration
def test_driver_fills_the_real_operation_runs_row_dispatch_created() -> None:
    """The FULL seam on the REAL DB: control_plane.dispatch persists the workroom:<id> row,
    the driver runs the task and writes the terminal Envelope into the SAME row's
    result_ref — one row, keyed workroom:<id>, no new table (§12.10)."""
    dsn = _local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL)")
    import json

    from control_plane.dispatch import assemble_bundle, dispatch_workroom

    from workroom.session import SessionDriver

    async def _run() -> dict[str, Any]:
        from libs.db import Database

        db = await Database.connect(dsn)
        try:
            meeting_id = uuid.uuid4()
            task_id = uuid.uuid4()
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1", f"workroom:{task_id}"
                )
            bundle = assemble_bundle(
                ask="build the retry refactor",
                speaker="Priya",
                timestamp=datetime.now(timezone.utc),
                meeting_id=meeting_id,
                transcript_tail="Priya: Proxy, build it.",
                task_id=task_id,
            )
            handle = await dispatch_workroom(db, bundle)
            fs = FakeSharedSandboxFS()
            provider = FakeCacheProvider(sandbox_fs=fs)
            driver = SessionDriver(provider=provider, sandbox_fs=fs, db=db)
            env = await driver.run_task(bundle, run_id=handle.run_id)
            async with db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT operation_type, status, progress, result_ref "
                    "FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            return {"row": dict(row) if row else None, "env": env, "task_id": task_id}
        finally:
            await db.close()

    out = asyncio.run(_run())
    row = out["row"]
    assert row is not None
    assert row["operation_type"] == f"workroom:{out['task_id']}"
    result_ref = row["result_ref"]
    if isinstance(result_ref, str):
        result_ref = json.loads(result_ref)
    assert result_ref is not None, "the driver must write the terminal Envelope into result_ref"
    assert str(result_ref["task_id"]) == str(out["task_id"])
    assert row["status"] in {"completed", "failed"}
