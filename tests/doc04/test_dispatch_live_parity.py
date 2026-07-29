"""TEST 11 · live-path parity — the in-meeting wake turn dispatches and gets re-woken.

This file exists because of a real bug it now guards. ``assemble_live_brain`` builds the
wake turn (and therefore mounts the MCP servers) BEFORE it builds the run loop — it must,
because ``build_run_loop`` needs the wake adapter the wake turn produces. The first version
of ``_build_dispatch_server`` read ``runtime.run_loop`` eagerly, found ``None`` every time,
and **silently declined to mount the dispatch tool on every live meeting**. Nothing failed;
Proxy simply had no way to do real work, forever.

So these tests assert the mount in the REAL assembly order rather than against a
hand-arranged runtime, and they assert the absence of a run loop is LOUD.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import pytest
from contracts import Envelope
from harness.dispatch import DISPATCH_SERVER_NAME, make_dispatch_workroom_tool
from harness.dispatch_sinks import RunLoopUnavailable, live_sink

pytestmark = pytest.mark.asyncio

MEETING = uuid.uuid4()


class _Loop:
    """Minimal stand-in for RunLoop: the sink only ever touches ``.queue``."""

    def __init__(self, maxsize: int = 0):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)


class _Runtime:
    """A runtime whose ``run_loop`` appears LATE, exactly as the real one does."""

    def __init__(self, db=object()):
        self.db = db
        self.run_loop = None  # not built yet — this is the real state at mount time

        class _H:
            meeting_id = str(MEETING)

        self.header = _H()

    def build_run_loop(self):
        self.run_loop = _Loop()
        return self.run_loop


def env(status="done", task_id=None):
    return Envelope(
        headline="h", detail="d", receipts=["retry.py:41"], status=status,
        task_id=task_id or uuid.uuid4(),
    )


# ── the mount survives the real assembly order ────────────────────────────
async def test_the_tool_mounts_even_though_run_loop_is_none_at_mount_time():
    """The regression this file exists for."""
    from harness.live_brain import _build_dispatch_server

    rt = _Runtime()
    assert rt.run_loop is None, "the premise: no run loop yet"
    servers = _build_dispatch_server(rt)
    assert servers and DISPATCH_SERVER_NAME in servers, (
        "the dispatch tool did not mount — an eager runtime.run_loop read has returned"
    )


async def test_build_servers_mounts_dispatch_alongside_code_intel():
    from harness.live_brain import _build_servers

    rt = _Runtime()
    rt.code_intel_ctx = None  # unindexed repo: no code_intel, still a dispatch verb
    servers = _build_servers(rt)
    assert servers and DISPATCH_SERVER_NAME in servers


async def test_assemble_order_is_wake_turn_before_run_loop():
    """Pins the ordering the lazy sink exists to tolerate.

    If someone reorders ``assemble_live_brain`` so the loop is built first, this test
    should be revisited deliberately — not silently satisfied.
    """
    import inspect

    from harness import live_brain

    src = inspect.getsource(live_brain.assemble_live_brain)
    assert src.index("build_wake_turn") < src.index("build_run_loop"), (
        "assembly order changed; the lazy run-loop resolution may no longer be needed"
    )


async def test_the_mount_does_not_read_run_loop_at_build_time():
    """Structural: an eager read is the bug, so assert it is not there."""
    import inspect

    from harness import live_brain

    src = inspect.getsource(live_brain._build_dispatch_server)
    body = src.split('"""', 2)[-1]  # skip the docstring, which discusses run_loop
    assert 'getattr(runtime, "run_loop"' not in body.split("_on_complete")[0], (
        "run_loop is read before the sink — the silent no-mount bug is back"
    )


# ── a missing run loop at completion time is LOUD ─────────────────────────
@pytest.mark.negative
async def test_a_missing_run_loop_at_completion_is_logged_not_swallowed(caplog):
    tid = uuid.uuid4()
    sink = live_sink(lambda: None, task_id=tid)
    with caplog.at_level(logging.ERROR):
        sink(env())
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "a dropped completion passed quietly"
    )
    assert any("will not hear" in r.message or "will not hear" in str(r.msg)
               for r in caplog.records), "the log does not say the room misses the result"
    assert issubclass(RunLoopUnavailable, RuntimeError)


@pytest.mark.negative
async def test_a_run_loop_without_a_queue_is_also_loud(caplog):
    sink = live_sink(lambda: object(), task_id=uuid.uuid4())
    with caplog.at_level(logging.ERROR):
        sink(env())
    assert any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.negative
async def test_a_full_queue_is_logged_and_the_run_is_not_lost(caplog):
    loop = _Loop(maxsize=1)
    loop.queue.put_nowait("occupied")
    sink = live_sink(lambda: loop, task_id=uuid.uuid4())
    with caplog.at_level(logging.ERROR):
        sink(env())
    assert any("durable" in str(r.msg) or "durable" in r.message for r in caplog.records), (
        "a full queue must say the result is still durable on the run row"
    )


# ── the re-wake actually happens ──────────────────────────────────────────
async def test_the_completion_lands_on_the_meeting_queue_for_the_emitter_rewake():
    """§3.2: the runtime delivers the done-moment. The envelope must reach the loop."""
    rt = _Runtime()
    rt.build_run_loop()  # the loop exists by completion time, as it does live
    tid = uuid.uuid4()
    e = env(status="done", task_id=tid)

    live_sink(lambda: rt.run_loop, task_id=tid)(e)

    assert rt.run_loop.queue.qsize() == 1
    event = rt.run_loop.queue.get_nowait()
    assert event.payload is e
    assert event.ask_id == str(tid), "the re-wake must be attributable to its ask"


async def test_end_to_end_the_wake_turn_dispatches_and_is_rewoken():
    """Tool call → run → completion → MeetingEvent on the loop. The full live loop."""
    rt = _Runtime()
    finished = asyncio.Event()

    async def run_task(bundle, *, run_id):
        await asyncio.sleep(0)
        return env(status="needs_review", task_id=bundle.task_id)

    def on_complete(envelope):
        live_sink(lambda: rt.run_loop, task_id=envelope.task_id)(envelope)
        finished.set()

    class _Conn:
        async def fetchrow(self, *a, **k):
            return {"id": uuid.uuid4()}

        async def fetchval(self, *a, **k):
            return uuid.uuid4()

    class _DB:
        instance_id = "test"

        def acquire(self):
            class _C:
                async def __aenter__(self_inner):
                    return _Conn()

                async def __aexit__(self_inner, *a):
                    return False

            return _C()

    from datetime import datetime, timezone

    tool_obj = make_dispatch_workroom_tool(
        db=_DB(), meeting_id=MEETING, now=lambda: datetime.now(timezone.utc),
        run_task=run_task, on_complete=on_complete,
    )

    # The wake turn calls the tool. It returns immediately.
    result = await tool_obj.handler({"ask": "bump the retry ceiling on checkout to 5"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["accepted"] is True
    dispatched_task = uuid.UUID(payload["task_id"])

    # The run loop is built AFTER the dispatch — the real ordering.
    rt.build_run_loop()

    await asyncio.wait_for(finished.wait(), timeout=5)
    assert rt.run_loop.queue.qsize() == 1, "Proxy was never re-woken"
    event = rt.run_loop.queue.get_nowait()
    assert event.payload.task_id == dispatched_task
    assert event.payload.status == "needs_review", "the status must arrive unchanged"
    assert event.payload.receipts == ["retry.py:41"], "the receipts must survive the hop"
