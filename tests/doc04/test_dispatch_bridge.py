"""Acceptance — the harness→Workroom DISPATCH BRIDGE (Doc 04 §11.6 + Doc 05 §3.2/§3.9).

The confirmed hole this proves closed: a wake-turn model calling ``dispatch_workroom``
produced a ``TOOL_USE`` chunk the pure channel projector renders only as a "working…" tile
that ``_emit_frame`` drops — so the REAL ``harness.dispatch.dispatch_workroom`` was NEVER
called, no Workroom ran, and Proxy was never re-woken with the result.

This suite proves the bridge on the REAL dispatch path (non-tautological):

  * ``is_dispatch_tool_use`` recognizes a ``dispatch_workroom`` TOOL_USE chunk and rejects a
    ``speak`` tool call / a bare TEXT chunk;
  * ``handle_dispatch`` calls the REAL ``harness.dispatch.dispatch_workroom`` — the REAL claim
    path runs against a recording db and yields a REAL ``WorkroomHandle`` referencing the
    claimed row — ACKs "on it: …" through the gated emitter, hands the assembled Bundle to the
    injected driver's ``run_task``, and on completion delivers the terminal Envelope's headline
    + body back through the gated emitter (the §3.2 done-moment push, never a poll);
  * a fenced-out (not is_owner) emitter delivers NOTHING (Rule 6 / §3.7).

The driver is a FAKE (``run_task`` → a canned Envelope) so no sandbox and no live model call.
The db is a recording stand-in that runs the REAL claim SQL statements — so the dispatch code
under test is the real one, never a mock of it.
"""
from __future__ import annotations

import asyncio

import pytest

from contracts import AgentChunk, Envelope


# ── a recording db that runs the REAL claim path (no live Postgres) ───────────


class _FakeConn:
    """A connection stand-in: ``fetchrow`` returns a claimed row id (the INSERT ... RETURNING
    the real ``_claim_workroom_row`` runs), ``fetchval`` the fallback lookup. Records the SQL
    it saw so the test can assert the REAL claim statement ran (not a mock of dispatch)."""

    def __init__(self, statements: list[str]) -> None:
        self._statements = statements
        self.run_id = "run-42"

    async def fetchrow(self, sql: str, *args):
        self._statements.append(sql)
        # The real INSERT ... ON CONFLICT ... RETURNING id — hand back a claimed row id.
        return {"id": self.run_id}

    async def fetchval(self, sql: str, *args):  # pragma: no cover - fallback path
        self._statements.append(sql)
        return self.run_id


class _FakeDB:
    """A ``libs.db.Database`` stand-in exposing the two seams the real claim path touches:
    ``acquire()`` (async CM → a conn) and ``instance_id`` (the created_by fence value)."""

    instance_id = "inst-1"

    def __init__(self) -> None:
        self.statements: list[str] = []

    def acquire(self):
        conn = _FakeConn(self.statements)

        class _CM:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _CM()


# ── a fake SessionDriver: run_task → a canned terminal Envelope (no sandbox) ──


class _FakeDriver:
    """Records the bundle + run_id ``run_task`` received and returns a canned terminal
    Envelope — proving the bridge drove the Workroom task without any sandbox / model call."""

    def __init__(self, *, headline: str = "traced it: 3 call sites", detail: str = "checkout.py:42, api.py:7, worker.py:11") -> None:
        self.headline = headline
        self.detail = detail
        self.seen_bundle = None
        self.seen_run_id = None
        self.calls = 0

    async def run_task(self, bundle, *, run_id, access="readwrite", preflight_code_hash=None):
        self.calls += 1
        self.seen_bundle = bundle
        self.seen_run_id = run_id
        return Envelope(
            headline=self.headline,
            detail=self.detail,
            status="done",
            task_id=bundle.task_id,
        )


# ── a fake gated emitter recording the wire (is_owner fencing) ────────────────


class _FakeEmitter:
    def __init__(self, *, is_owner: bool = True) -> None:
        self.is_owner = is_owner
        self.spoken: list[str] = []

    def speak(self, text) -> bool:
        if not self.is_owner:
            return False
        self.spoken.append(text)
        return True


class _Header:
    # A REAL UUID meeting_id so the bundle's notes_ref cast succeeds (§1.3).
    meeting_id = "11111111-1111-1111-1111-111111111111"


class _Runtime:
    def __init__(self, db) -> None:
        self.header = _Header()
        self.db = db
        self.abort_registry = None


class _WakeEvent:
    def __init__(self, text: str = "Proxy, trace the refund path", speaker: str = "Sam") -> None:
        self.text = text
        self.speaker = speaker


def _dispatch_chunk(task: str = "trace the blast radius of renaming chargeCard"):
    return AgentChunk(
        type="TOOL_USE",
        metadata={"id": "tu-1", "name": "dispatch_workroom", "input": {"task": task}},
    )


# ── the predicate ────────────────────────────────────────────────────────────


def test_is_dispatch_tool_use_recognizes_only_the_dispatch_tool_call() -> None:
    from harness.workroom_bridge import is_dispatch_tool_use

    assert is_dispatch_tool_use(_dispatch_chunk()) is True
    # A different tool call is NOT a dispatch.
    assert is_dispatch_tool_use(
        AgentChunk(type="TOOL_USE", metadata={"name": "speak", "input": {"text": "hi"}})
    ) is False
    # A bare TEXT chunk (the model's reasoning) is NOT a dispatch.
    assert is_dispatch_tool_use(AgentChunk(type="TEXT", text="dispatch_workroom")) is False
    # A malformed chunk (no metadata dict) never crashes the predicate.
    assert is_dispatch_tool_use(AgentChunk(type="TOOL_USE")) is False


# ── the reactive flow on the REAL dispatch path ──────────────────────────────


@pytest.mark.asyncio
async def test_handle_dispatch_claims_real_row_acks_and_delivers_result() -> None:
    """``handle_dispatch`` runs the REAL ``dispatch_workroom`` claim, ACKs, drives the injected
    driver's ``run_task`` with the assembled Bundle, and delivers the terminal Envelope (§11.6)."""
    from harness.workroom_bridge import handle_dispatch

    db = _FakeDB()
    runtime = _Runtime(db)
    driver = _FakeDriver()
    emitter = _FakeEmitter(is_owner=True)
    tasks: set = set()

    await handle_dispatch(
        _dispatch_chunk(),
        runtime=runtime,
        event=_WakeEvent(),
        driver=driver,
        emitter=emitter,
        db=db,
        tasks=tasks,
    )

    # (1) The REAL claim path ran against the recording db — the INSERT ... operation_runs
    #     statement was executed (this is the real dispatch code, not a mock of it).
    assert any("operation_runs" in s and "INSERT" in s for s in db.statements), (
        "the REAL dispatch_workroom claim path did not run against the db"
    )

    # (2) The ACK was spoken immediately (the partial "on it: <ask>" headline, §3.2).
    assert emitter.spoken, "no ACK was spoken"
    assert emitter.spoken[0] == "on it: trace the blast radius of renaming chargeCard"

    # Let the background driver task run to completion.
    assert tasks, "the driver task was not tracked (would be GC'd mid-flight)"
    await asyncio.gather(*tasks)

    # (3) run_task received the assembled Bundle + the claimed run_id.
    assert driver.calls == 1, "the Workroom driver's run_task was not driven"
    assert driver.seen_run_id == "run-42", "run_task did not receive the claimed operation_runs id"
    assert driver.seen_bundle is not None
    assert driver.seen_bundle.ask == "trace the blast radius of renaming chargeCard"
    assert str(driver.seen_bundle.notes_ref) == _Header.meeting_id  # notes_ref = the meeting_id (§1.3)
    assert driver.seen_bundle.speaker == "Sam"

    # (4) On completion the terminal Envelope headline + body were delivered (the done-moment).
    assert "traced it: 3 call sites" in emitter.spoken, "the result headline was not delivered"
    assert "checkout.py:42, api.py:7, worker.py:11" in emitter.spoken, "the result body was not delivered"


@pytest.mark.asyncio
async def test_handle_dispatch_fenced_out_emitter_delivers_nothing() -> None:
    """A fenced-out (not is_owner) emitter delivers NOTHING — no ACK, no result (§3.7 / Rule 6)."""
    from harness.workroom_bridge import handle_dispatch

    db = _FakeDB()
    runtime = _Runtime(db)
    driver = _FakeDriver()
    emitter = _FakeEmitter(is_owner=False)  # a zombie — lost the fence
    tasks: set = set()

    await handle_dispatch(
        _dispatch_chunk(),
        runtime=runtime,
        event=_WakeEvent(),
        driver=driver,
        emitter=emitter,
        db=db,
        tasks=tasks,
    )
    if tasks:
        await asyncio.gather(*tasks)

    assert emitter.spoken == [], "a fenced-out harness must deliver nothing"


@pytest.mark.asyncio
async def test_handle_dispatch_non_uuid_meeting_id_skips_dispatch_without_raising() -> None:
    """A non-UUID meeting_id can't key a durable row — the dispatch is skipped honestly and the
    wake loop never sees an exception (Rule 6)."""
    from harness.workroom_bridge import handle_dispatch

    db = _FakeDB()
    runtime = _Runtime(db)
    runtime.header = type("_H", (), {"meeting_id": "mtg-1"})()  # a non-UUID id (the test fixture id)
    driver = _FakeDriver()
    emitter = _FakeEmitter(is_owner=True)
    tasks: set = set()

    # Must NOT raise, must claim NO row, must drive NO task.
    await handle_dispatch(
        _dispatch_chunk(),
        runtime=runtime,
        event=_WakeEvent(),
        driver=driver,
        emitter=emitter,
        db=db,
        tasks=tasks,
    )
    assert db.statements == [], "a non-UUID meeting_id must claim no row"
    assert driver.calls == 0, "a non-UUID meeting_id must drive no Workroom task"
    assert emitter.spoken == [], "a skipped dispatch speaks nothing"


@pytest.mark.asyncio
async def test_handle_dispatch_driver_fault_still_delivers_a_failed_envelope() -> None:
    """A driver that RAISES still delivers a failed Envelope through the gated emitter — the
    background task never surfaces an unhandled exception (Rule 6)."""
    from harness.workroom_bridge import handle_dispatch

    class _FaultyDriver:
        async def run_task(self, bundle, *, run_id, access="readwrite", preflight_code_hash=None):
            raise RuntimeError("sandbox exploded")

    db = _FakeDB()
    runtime = _Runtime(db)
    emitter = _FakeEmitter(is_owner=True)
    tasks: set = set()

    await handle_dispatch(
        _dispatch_chunk(),
        runtime=runtime,
        event=_WakeEvent(),
        driver=_FaultyDriver(),
        emitter=emitter,
        db=db,
        tasks=tasks,
    )
    assert tasks
    await asyncio.gather(*tasks)  # must not raise

    # The ACK plus a failed-Envelope delivery (an honest degrade, not a crash).
    assert any("couldn't finish" in s for s in emitter.spoken), "a driver fault must still deliver an honest failure"
