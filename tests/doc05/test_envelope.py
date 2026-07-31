"""Doc 05 · workroom.envelope — the ONE output contract + tool-boundary ProgressEvents
on the REAL host path (05 §3.12 / §3.13-step-5 / CANONICAL §1.2).

Spec refs: 05-WORKROOM.md §3.12 (the one output contract: a contracts.Envelope
``{headline (speakable), detail, artifact?, receipts, status ∈
{done|partial|failed|needs_clarification|needs_review}, verification? ∈
{verified|unverified} (builds only), draft_id?, task_id}`` back to Proxy; progress
events use the SAME shape minus finality), §3.13-step-5 (a quick ask AND a long build
both return a contract-conforming Envelope; a long build streams tool-boundary
ProgressEvents the harness receives), CANONICAL §1.2 (the status mapping — read-only
or applied+verified → done; staged draft awaiting a click → needs_review; a
critic/gate-failed build → failed; ``verified``/``draft`` are NEVER status values —
the proof state rides the optional ``verification`` field).

This is the value the ORCHESTRATOR receives back from a dispatch (the doc04 lesson):
Doc 04's ``control_plane.dispatch`` creates a REAL ``contracts.Bundle`` + a ``workroom:<id>``
operation_runs row and dispatches it; the ``SessionDriver`` the dispatch invokes
consumes that REAL Bundle and returns a REAL ``contracts.Envelope`` assembled HERE.

Definition of Done proven here:
  1. a QUICK ASK returns a contract-conforming Envelope (speakable headline, status
     per §1.2, task_id preserved) — reachable from the dispatch, not isolation-only.
  2. a LONG BUILD streams tool-boundary ProgressEvents the harness receives — one per
     REAL tool boundary (from the tool-use stream, NEVER model prose / TEXT), each the
     Envelope shape MINUS finality (no terminal status field).
  3. the status/verification mapping is exactly §1.2: verified/draft are NEVER status
     values; the build's proof state rides the optional ``verification`` field; a staged
     draft → needs_review; a failed build → failed.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest

# Import the contract types from the top-level ``contracts`` module (matches the product:
# control_plane.dispatch + session return objects from ``contracts``; ``libs.contracts`` is a
# DISTINCT module identity under the test src-wiring → isinstance would fail).
from contracts import AgentChunk, Bundle, Envelope, ProgressEvent


# ── shared helpers ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_sandbox_provider_state() -> Any:
    """Isolate the global sandbox-provider registry per test (the meeting→sandbox map).

    Sibling doc05 test modules (preflight / cost) provision sandboxes into the shared
    ``sandbox_provider`` module globals; reset before AND after so this node's tests are
    never a victim of — nor a source of — cross-module pollution (§3.4 isolation)."""
    from libs.ops import sandbox_provider

    sandbox_provider._reset_for_test()
    yield
    sandbox_provider._reset_for_test()


def _bundle(ask: str, *, task_id: uuid.UUID | None = None) -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        notes_ref=uuid.uuid4(),
        transcript_tail=f"Sam: Proxy, {ask}.",
        task_id=task_id or uuid.uuid4(),
    )


# ══ clause 1: build_envelope assembles a contract-conforming Envelope ══════════


def test_build_envelope_is_contract_conforming_for_a_quick_ask() -> None:
    """DoD #1: a quick ask assembles a contract-conforming ``contracts.Envelope`` —
    a speakable headline, status per §1.2, the bundle's task_id preserved (§3.12)."""
    from workroom.envelope import build_envelope

    b = _bundle("where is the retry logic")
    env = build_envelope(
        bundle=b,
        result_meta={"total_cost_usd": 0.001, "session_id": "sess-1"},
        wrote_paths=[],
        receipts=["read libs/http/external.py:42 — call_external seam"],
    )

    assert isinstance(env, Envelope), "build_envelope must return a real contracts.Envelope"
    assert env.task_id == b.task_id, "the Envelope must carry the bundle's task_id"
    # A read-only answer is `done` (§1.2).
    assert env.status == "done"
    # The headline is spoken in a meeting — present and speakable (a sentence or two).
    assert env.headline, "the headline is spoken aloud — it must be present"
    assert len(env.headline) <= 240, "a spoken headline stays ≤ a sentence or two"
    # It round-trips through the pydantic contract (no drift → no silent extra field).
    reparsed = Envelope.model_validate(env.model_dump(mode="json"))
    assert reparsed.status == "done"
    assert reparsed.task_id == b.task_id


def test_build_envelope_carries_receipts_and_artifact_for_a_build() -> None:
    """DoD #1: a build that wrote a file carries its receipts + artifact and a `Built:`
    speakable headline — still contract-conforming (§3.12)."""
    from workroom.envelope import build_envelope

    b = _bundle("build the ratelimiter")
    env = build_envelope(
        bundle=b,
        result_meta={"total_cost_usd": 0.03, "session_id": "s"},
        wrote_paths=["lib/ratelimit.py"],
        receipts=["wrote lib/ratelimit.py (42 bytes)"],
        artifact_extra={"files": ["lib/ratelimit.py"]},
    )

    assert env.status == "done"
    assert "lib/ratelimit.py" in (env.artifact or {}).get("files", [])
    assert any("ratelimit" in r for r in env.receipts)
    assert env.headline.startswith("Built") or "ratelimit" in env.headline


# ══ clause 2: status / verification map EXACTLY per §1.2 ═══════════════════════


def test_verified_and_draft_are_never_status_values() -> None:
    """DoD #3 (the hard NOT-done): ``verified``/``draft`` are NEVER EnvelopeStatus
    members — the build's proof state rides the optional ``verification`` field, never
    smuggled into ``status`` (CANONICAL §1.2)."""
    from contracts.envelopes import EnvelopeStatus
    from typing import get_args

    members = set(get_args(EnvelopeStatus))
    assert "verified" not in members, "'verified' must NOT be a status value (§1.2)"
    assert "draft" not in members, "'draft' must NOT be a status value (§1.2)"
    assert members == {"done", "partial", "failed", "needs_clarification", "needs_review"}


def test_status_mapping_read_only_and_applied_verified_are_done() -> None:
    """DoD #3 (§1.2): a read-only answer → done; an applied+verified build → done +
    verification='verified' (proof state rides the verification field, not status)."""
    from workroom.envelope import map_status_verification

    # read-only answer (no build, no verification result)
    ro = map_status_verification(is_build=False, verified=None, has_draft=False)
    assert ro == ("done", None)

    # applied + verified build
    av = map_status_verification(is_build=True, verified=True, has_draft=False)
    assert av == ("done", "verified"), "an applied+verified build → done + verification='verified'"


def test_status_mapping_staged_draft_is_needs_review_not_a_status_named_draft() -> None:
    """DoD #3 (§1.2): a staged draft awaiting a human click → status='needs_review' +
    verification='unverified' — NEVER a status literally named 'draft'/'verified'."""
    from workroom.envelope import map_status_verification

    status, verification = map_status_verification(is_build=True, verified=False, has_draft=True)
    assert status == "needs_review", "a staged draft awaiting a click → needs_review (§1.2)"
    assert verification == "unverified", "an unverified staged build rides verification='unverified'"
    assert status not in {"verified", "draft"}


def test_status_mapping_failed_build_is_failed() -> None:
    """DoD #3 (§1.2): a build the critic/evidence-gate failed → status='failed' +
    verification='unverified'."""
    from workroom.envelope import map_status_verification

    status, verification = map_status_verification(
        is_build=True, verified=False, has_draft=False, failed=True
    )
    assert status == "failed"
    assert verification == "unverified"


def test_build_envelope_honours_the_status_mapping_end_to_end() -> None:
    """DoD #3: build_envelope threads the §1.2 mapping — a staged-draft build produces
    a needs_review Envelope carrying verification='unverified' + the draft_id, and
    verified/draft never appear as the status value."""
    from workroom.envelope import build_envelope

    b = _bundle("push this change")
    draft_id = uuid.uuid4()
    env = build_envelope(
        bundle=b,
        result_meta={},
        wrote_paths=["api/routes.py"],
        receipts=["staged draft api/routes.py"],
        is_build=True,
        verified=False,
        draft_id=draft_id,
    )
    assert env.status == "needs_review", "a staged draft → needs_review (§1.2)"
    assert env.verification == "unverified"
    assert env.draft_id == draft_id
    assert env.status not in {"verified", "draft"}
    # Contract round-trips.
    Envelope.model_validate(env.model_dump(mode="json"))


# ══ clause 3: ProgressEvent = the Envelope shape MINUS finality ════════════════


def test_progress_event_for_a_real_tool_boundary_carries_no_finality() -> None:
    """DoD #2: a ProgressEvent is minted from a REAL tool boundary (a TOOL_USE chunk),
    is the Envelope shape MINUS finality (NO status field at all), and names the tool
    that ran (§3.12 — progress events use the same shape minus finality)."""
    from workroom.envelope import progress_event_for_chunk

    task_id = uuid.uuid4()
    # A real tool boundary from stream_deltas: chunk.type == 'TOOL_USE', name in metadata.
    chunk = AgentChunk(
        type="TOOL_USE",
        metadata={"name": "mcp__code__run_command", "input": {"command": "pytest -q"}, "id": "t1"},
    )
    ev = progress_event_for_chunk(chunk, task_id)

    assert isinstance(ev, ProgressEvent), "a tool boundary must mint a real contracts.ProgressEvent"
    assert ev.task_id == task_id
    assert "run_command" in ev.headline, "the progress headline names the tool that ran (a boundary)"
    # A ProgressEvent has NO status field — it is the Envelope shape MINUS finality (§3.12).
    assert not hasattr(ev, "status"), "a ProgressEvent must carry NO terminal status (minus finality)"


def test_progress_is_read_from_tool_boundaries_never_model_prose() -> None:
    """DoD #2 (the hard NOT-done): progress is derived from the REAL tool-use stream,
    NEVER from the model's TEXT prose. A TEXT chunk mints NO ProgressEvent; only a real
    TOOL_USE boundary does (§3.12 / node invariant 'consume stream_deltas, never raw
    AgentChunk; progress from real tool boundaries')."""
    from workroom.envelope import progress_event_for_chunk

    task_id = uuid.uuid4()
    # The model narrating "I ran the tests and they passed" is PROSE — it must NOT
    # become a progress event (that is exactly the receipts-from-prose anti-pattern).
    text = AgentChunk(type="TEXT", text="I ran pytest and it passed", metadata={"msg_id": "m1"})
    assert progress_event_for_chunk(text, task_id) is None, "model prose must NOT mint a progress event"

    # An INIT / RESULT / TOOL_RESULT frame is not a tool boundary either → no progress.
    for t in ("INIT", "RESULT", "TOOL_RESULT", "ERROR"):
        c = AgentChunk(type=t, metadata={})
        assert progress_event_for_chunk(c, task_id) is None, f"a {t} chunk is not a tool boundary"


def test_emit_tool_boundary_progress_streams_one_event_per_tool_call() -> None:
    """DoD #2: draining a stream through ``emit_tool_boundary_progress`` streams ONE
    ProgressEvent to the harness sink per REAL tool boundary (two tool calls → two
    progress events), passing every chunk through untouched for the terminal fold."""
    from workroom.envelope import emit_tool_boundary_progress

    task_id = uuid.uuid4()

    async def _fake_stream() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(type="INIT", metadata={"session_id": "s"})
        yield AgentChunk(type="TOOL_USE", metadata={"name": "mcp__code__grep", "id": "t1"})
        yield AgentChunk(type="TEXT", text="looking...", metadata={"msg_id": "m1"})
        yield AgentChunk(type="TOOL_USE", metadata={"name": "mcp__code__write_file",
                                                    "input": {"path": "x.py"}, "id": "t2"})
        yield AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01})

    received: list[ProgressEvent] = []

    async def _sink(ev: ProgressEvent) -> None:
        received.append(ev)

    async def _run() -> list[AgentChunk]:
        passed_through: list[AgentChunk] = []
        async for chunk in emit_tool_boundary_progress(_fake_stream(), task_id, _sink):
            passed_through.append(chunk)
        return passed_through

    passed_through = asyncio.run(_run())

    # Exactly the two TOOL_USE boundaries minted progress — not the INIT/TEXT/RESULT.
    assert len(received) == 2, "one ProgressEvent per REAL tool boundary (two tool calls)"
    assert all(isinstance(ev, ProgressEvent) for ev in received)
    assert "grep" in received[0].headline
    assert "write_file" in received[1].headline
    assert all(rt.task_id == task_id for rt in received)
    # Every chunk passed through untouched (the terminal fold still sees INIT/RESULT).
    assert [c.type for c in passed_through] == ["INIT", "TOOL_USE", "TEXT", "TOOL_USE", "RESULT"]


def test_emit_tool_boundary_progress_never_raises_on_a_sink_fault() -> None:
    """Rule 6 (never-throw): a progress sink that raises must NOT abort the run — the
    stream keeps flowing and the terminal fold still completes (a partial receipt beats
    a crash mid-build)."""
    from workroom.envelope import emit_tool_boundary_progress

    task_id = uuid.uuid4()

    async def _fake_stream() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(type="TOOL_USE", metadata={"name": "mcp__code__grep", "id": "t1"})
        yield AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01})

    async def _bad_sink(ev: ProgressEvent) -> None:
        raise RuntimeError("the harness progress channel hiccuped")

    async def _run() -> list[str]:
        seen: list[str] = []
        async for chunk in emit_tool_boundary_progress(_fake_stream(), task_id, _bad_sink):
            seen.append(chunk.type)
        return seen

    seen = asyncio.run(_run())
    # The whole stream still drained despite the sink fault (never-throw boundary).
    assert seen == ["TOOL_USE", "RESULT"]


# ══ clause 4: the REAL seam — the driver produces progress + the Envelope ══════


def test_driver_streams_tool_boundary_progress_and_returns_the_envelope() -> None:
    """DoD #2 + #3 (the live-assembly seam): the REAL ``SessionDriver.run_task`` — the
    path the harness dispatch invokes — streams tool-boundary ProgressEvents to an
    injected harness sink AND returns the terminal contracts.Envelope. A long build with
    a tool call streams a ProgressEvent the harness receives; the Envelope is
    contract-conforming (§3.13-step-5)."""
    from workroom.session import SessionDriver

    # A minimal in-process provider that streams a real tool boundary then a RESULT frame.
    class _ToolStreamProvider:
        name = "claude"

        def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
            return True

        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": "sess-1"})
                yield AgentChunk(
                    type="TOOL_USE",
                    metadata={"name": "mcp__code__write_file", "input": {"path": "lib/x.py"}, "id": "w1"},
                )
                yield AgentChunk(type="TEXT", text="wrote it", metadata={"msg_id": "m1"})
                yield AgentChunk(
                    type="RESULT",
                    metadata={"session_id": "sess-1", "num_turns": 1, "total_cost_usd": 0.02,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 4000},
                )

            return gen()

    class _FakeFS:
        def __init__(self) -> None:
            self._files = {"lib/x.py": b"X = 1\n"}

        async def read_bytes(self, path: str) -> bytes | None:
            return self._files.get(path)

    class _FakeStore:
        def __init__(self) -> None:
            self.rows: dict[str, Any] = {}

        def claim(self, *, run_id: str, operation_type: str, progress: dict[str, Any]) -> None:
            self.rows[run_id] = {"result_ref": None, "status": "running"}

        async def set_result(self, *, run_id: str, result_ref: dict[str, Any], status: str) -> None:
            self.rows[run_id] = {"result_ref": dict(result_ref), "status": status}

    from libs.ops import sandbox_provider

    sandbox_provider._reset_for_test()
    try:
        b = _bundle("build the x module")
        # Meeting-creation pre-provision (§3.9): the meeting's ONE warm sandbox is live
        # BEFORE the task runs — so the §3.9 preflight ("never cold-boot mid-meeting")
        # passes and the driver reaches the query() (the real dispatch flow pre-provisions).
        sandbox_provider.provision(meeting_id=str(b.notes_ref))
        store = _FakeStore()
        run_id = uuid.uuid4().hex
        store.claim(run_id=run_id, operation_type=f"workroom:{b.task_id}", progress=b.model_dump(mode="json"))

        received: list[ProgressEvent] = []

        async def _sink(ev: ProgressEvent) -> None:
            received.append(ev)

        driver = SessionDriver(
            provider=_ToolStreamProvider(),
            sandbox_fs=_FakeFS(),
            store=store,
            on_progress=_sink,
        )
        env = asyncio.run(driver.run_task(b, run_id=run_id))
    finally:
        sandbox_provider._reset_for_test()

    # The Envelope is the REAL contract, carrying the bundle's task_id (the 05→04 return).
    assert isinstance(env, Envelope)
    assert env.task_id == b.task_id
    assert env.status in {"done", "partial", "failed", "needs_clarification", "needs_review"}
    assert env.headline
    # The harness received a tool-boundary ProgressEvent (from the REAL tool-use stream).
    assert received, "the harness sink must receive at least one tool-boundary ProgressEvent"
    assert all(isinstance(ev, ProgressEvent) for ev in received)
    assert any("write_file" in ev.headline for ev in received), "progress names the real tool boundary"
    # Progress carried NO terminal status (it is the Envelope shape MINUS finality).
    assert all(not hasattr(ev, "status") for ev in received)


def test_envelope_module_derives_progress_from_tool_use_never_text() -> None:
    """DoD #2 (the hard NOT-done, structural): the envelope module derives progress from
    the TOOL_USE boundary, never from TEXT prose — no code path mints a ProgressEvent
    off a TEXT chunk. Proven by a stream of ONLY prose yielding ZERO progress events."""
    from workroom.envelope import emit_tool_boundary_progress

    task_id = uuid.uuid4()

    async def _prose_only() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(type="INIT", metadata={"session_id": "s"})
        yield AgentChunk(type="TEXT", text="I am running the tests now", metadata={"msg_id": "m1"})
        yield AgentChunk(type="TEXT", text="the tests passed, exit 0", metadata={"msg_id": "m1"})
        yield AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.001})

    received: list[ProgressEvent] = []

    async def _sink(ev: ProgressEvent) -> None:
        received.append(ev)

    async def _run() -> None:
        async for _ in emit_tool_boundary_progress(_prose_only(), task_id, _sink):
            pass

    asyncio.run(_run())
    assert received == [], "a prose-only stream (no tool boundary) must mint ZERO progress events"


def test_envelope_module_never_imports_e2b() -> None:
    """The host-side logic is proven against in-process fakes; e2b is NOT installed and
    MUST NOT be imported by the envelope module (the E2B client is lazy behind
    call_external; the template bake is the flagged Phase-3 residual)."""
    import workroom.envelope as mod

    src = inspect.getsource(mod)
    assert "import e2b" not in src and "from e2b" not in src, "envelope.py must never import e2b"
