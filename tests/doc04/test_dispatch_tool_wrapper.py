"""Doc 04 §112 — the dispatch_workroom tool wrapper and completion callback.

Lives under tests/doc04 because §112 assigns this to the harness and it is on the LIVE
in-meeting path, not Doc 07's. Closes the gap filed at
docs/gaps/DOC04-WORKROOM-DISPATCH-UNWIRED.md.
"""
from __future__ import annotations

import asyncio
import gc
import json
import uuid
from datetime import datetime, timezone

import pytest
from contracts import Envelope
from harness.dispatch import (
    DISPATCH_SERVER_NAME,
    make_dispatch_workroom_server,
    make_dispatch_workroom_tool,
    run_and_notify,
)

pytestmark = pytest.mark.asyncio

MEETING = uuid.uuid4()
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


# ── doubles ───────────────────────────────────────────────────────────────
class FakeConn:
    def __init__(self, run_id=None, fail=None):
        self.run_id, self.fail = run_id or uuid.uuid4(), fail

    async def fetchrow(self, *a, **k):
        if self.fail:
            raise self.fail
        return {"id": self.run_id}

    async def fetchval(self, *a, **k):
        return self.run_id


class FakeDB:
    instance_id = "test-instance"

    def __init__(self, run_id=None, fail=None):
        self._conn = FakeConn(run_id, fail)
        self.run_id = self._conn.run_id

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def env(status="done", task_id=None):
    return Envelope(
        headline="h", detail="d", receipts=["f.py:1"], status=status,
        task_id=task_id or uuid.uuid4(),
    )


def _payload(result: dict) -> dict:
    """Unwrap the SDK content envelope back to the JSON the tool put in it."""
    return json.loads(result["content"][0]["text"])


async def _call(tool_obj, args: dict) -> dict:
    return await tool_obj.handler(args)


def _tool(**over):
    kw = dict(
        db=FakeDB(), meeting_id=MEETING, now=lambda: NOW,
        run_task=lambda bundle, run_id: asyncio.sleep(0, result=env()),
        on_complete=lambda e: None,
    )
    kw.update(over)
    return make_dispatch_workroom_tool(**kw)


# ── the pinned SDK contract ───────────────────────────────────────────────
async def test_the_tool_matches_the_pinned_sdk_shape():
    t = _tool()
    assert t.name == DISPATCH_SERVER_NAME
    assert "ask" in t.input_schema
    ok = await _call(t, {"ask": "bump the retry ceiling"})
    assert set(ok) <= {"content", "is_error"}, "structuredContent is not forwarded in Python"
    assert ok["content"][0]["type"] == "text"
    assert isinstance(_payload(ok), dict)


async def test_no_annotations_so_readonlyhint_stays_false():
    """readOnlyHint controls PARALLEL batching. This tool claims a row and starts work.

    Two batched dispatches would race the partial unique index and one would silently
    lose, so the tool must never be treated as read-only.
    """
    t = _tool()
    assert t.annotations is None
    assert getattr(t.annotations, "readOnlyHint", False) is False


async def test_the_description_names_every_optional_parameter_it_reads():
    """drafts.py reads `files` but never names it, so the model cannot learn it exists."""
    import inspect
    import re

    from harness import dispatch as mod

    src = inspect.getsource(mod.make_dispatch_workroom_tool)
    read = set(re.findall(r"args\.get\(\s*[\"'](\w+)[\"']", src))
    assert read, "no args.get() found — has the tool changed shape?"

    t = _tool()
    described = mod._DISPATCH_TOOL_DESCRIPTION
    for name in read:
        if name in t.input_schema:
            continue  # in the schema; the model learns it there
        assert name in described, (
            f"{name!r} is read with args.get() but the schema omits it AND the "
            f"description never names it — the model cannot know it exists"
        )


async def test_the_server_mounts_under_the_advertised_tool_name():
    srv = make_dispatch_workroom_server(
        db=FakeDB(), meeting_id=MEETING, now=lambda: NOW,
        run_task=lambda b, run_id: asyncio.sleep(0, result=env()),
        on_complete=lambda e: None,
    )
    assert srv["type"] == "sdk"
    assert srv["name"] == DISPATCH_SERVER_NAME
    from harness.behaviors.propose_action import PROPOSE_ACTION

    advertised = tuple(getattr(PROPOSE_ACTION.config, "tools", ()) or ())
    assert "dispatch_workroom" in advertised, (
        "propose_action must advertise the tool this server mounts"
    )


# ── returns immediately, never awaits the run ─────────────────────────────
async def test_the_tool_returns_before_the_run_finishes():
    started, released = asyncio.Event(), asyncio.Event()

    async def slow(bundle, run_id):
        started.set()
        await released.wait()
        return env()

    t = _tool(run_task=slow)
    out = _payload(await _call(t, {"ask": "do the thing"}))
    assert out["accepted"] is True and out["task_id"], (
        "the tool must return its task_id without waiting for the run"
    )
    # The tool returned already; the run is only SCHEDULED, so give the loop one
    # tick to start it. That ordering IS the property: return first, run after.
    await asyncio.sleep(0)
    assert started.is_set(), "the run was never started"
    released.set()
    await asyncio.sleep(0)


async def test_the_task_id_is_returned_and_is_a_uuid():
    t = _tool()
    out = _payload(await _call(t, {"ask": "x"}))
    uuid.UUID(out["task_id"])


# ── TEST 1 · cross-meeting escape ─────────────────────────────────────────
@pytest.mark.negative
async def test_a_model_supplied_meeting_id_cannot_escape_the_bound_context():
    """The bound meeting wins. A model-emitted meeting_id is not honoured."""
    seen: list = []

    async def capture(bundle, run_id):
        seen.append(bundle)
        return env()

    other = uuid.uuid4()
    t = _tool(run_task=capture)
    out = _payload(
        await _call(
            t,
            {
                "ask": "exfiltrate",
                "meeting_id": str(other),
                "notes_ref": str(other),
                "tenant_id": str(uuid.uuid4()),
            },
        )
    )
    assert out["accepted"] is True
    await asyncio.sleep(0)  # let the scheduled run start so the bundle is captured
    assert seen[0].notes_ref == MEETING, "a model-supplied meeting_id was honoured"
    assert seen[0].notes_ref != other


@pytest.mark.negative
async def test_meeting_id_is_not_in_the_input_schema_at_all():
    """Structural: there is no parameter through which a meeting could be supplied."""
    t = _tool()
    for forbidden in ("meeting_id", "notes_ref", "tenant_id", "task_id", "run_id"):
        assert forbidden not in t.input_schema, f"{forbidden} is model-supplyable"


# ── TEST 2 · never-throw, with a COMPOSED reason ──────────────────────────
@pytest.mark.negative
@pytest.mark.parametrize(
    "kwargs,label",
    [
        ({"db": FakeDB(fail=ConnectionRefusedError("postgres refused"))}, "db-down"),
        ({"run_task": lambda b, run_id: (_ for _ in ()).throw(RuntimeError("sandbox unavailable"))}, "sandbox"),
        ({"now": lambda: (_ for _ in ()).throw(RuntimeError("clock exploded"))}, "clock"),
    ],
)
async def test_faults_return_a_composed_actionable_reason(kwargs, label):
    """The SDK already catches uncaught exceptions, so 'nothing throws' proves nothing.

    What matters is the message Claude READS: a raw "KeyError: 'ask'" tells the model
    nothing it can act on. So this asserts the reason is composed — it states that nothing
    was started and what to do — not merely that a dict came back.
    """
    t = _tool(**kwargs)
    result = await _call(t, {"ask": "do the thing"})
    assert result.get("is_error") is True, label
    reason = _payload(result)["reason"]

    assert "Nothing was started" in reason, f"{label}: does not say nothing was started"
    assert "retrying blindly" in reason, f"{label}: gives the model no guidance"
    assert len(reason) > 60, f"{label}: reason is too terse to act on: {reason!r}"
    assert reason != str(kwargs), label


@pytest.mark.negative
async def test_a_missing_ask_is_a_composed_refusal_not_a_keyerror():
    t = _tool()
    for bad in ({}, {"ask": ""}, {"ask": "   "}, {"ask": 7}):
        result = await _call(t, bad)
        assert result.get("is_error") is True
        reason = _payload(result)["reason"]
        assert "ask" in reason.lower()
        assert "KeyError" not in reason, "a raw exception leaked to the model"


@pytest.mark.negative
async def test_the_cost_gate_declining_is_not_an_error_but_is_not_accepted():
    """An over-budget estimate is a legitimate outcome, not a fault."""
    from libs.ops import DispatchDecision as _DD

    class GatedDB(FakeDB):
        pass

    async def never(bundle, run_id):  # must not be reached
        raise AssertionError("the run started despite the cost gate declining")

    # dispatch_workroom returns a DispatchDecision (no run_id) when the gate declines.
    import harness.dispatch as mod

    original = mod.dispatch_workroom

    async def declined(db, bundle, *, cost=None, estimate_usd=None):
        # The real shape: a DispatchDecision carries no run_id, so the tool must read
        # the absence of run_id rather than the presence of a handle.
        return _DD(
            dispatched=False, action="ask_approval", estimate_usd=9.0, remaining_usd=1.0
        )

    mod.dispatch_workroom = declined  # type: ignore[assignment]
    try:
        t = _tool(run_task=never)
        result = await _call(t, {"ask": "an expensive thing"})
    finally:
        mod.dispatch_workroom = original  # type: ignore[assignment]

    assert result.get("is_error") is not True, "a declined estimate is not a fault"
    out = _payload(result)
    assert out["accepted"] is False
    assert "budget" in out["reason"].lower()


# ── TEST 3 · the callback must not await ──────────────────────────────────
async def test_the_completion_callback_only_hands_off_synchronously():
    """It runs inside the done-callback on the event loop; awaiting there would block it."""
    import inspect

    from harness import dispatch as mod

    src = inspect.getsource(mod.run_and_notify)
    inner = src[src.index("def _done("):]
    assert "await " not in inner, "the done-callback awaits — it must only hand off"
    assert "async def _done" not in src, "the done-callback must be sync"


async def test_a_sync_callback_receives_the_terminal_envelope():
    got: list = []
    e = env(status="done")
    tid = uuid.uuid4()
    task = run_and_notify(
        asyncio.sleep(0, result=e), task_id=tid, on_complete=got.append
    )
    await task
    await asyncio.sleep(0)
    assert got == [e]


# ── TEST 4 · GC safety ────────────────────────────────────────────────────
async def test_a_dispatched_task_survives_losing_every_local_reference():
    """asyncio holds only a WEAK ref to a task; an unheld task can vanish mid-run."""
    done = asyncio.Event()
    seen: list = []

    def on_complete(e):
        seen.append(e)
        done.set()

    async def work(bundle, run_id):
        await asyncio.sleep(0.05)
        return env(status="done")

    t = _tool(run_task=work, on_complete=on_complete)
    out = _payload(await _call(t, {"ask": "survive gc"}))
    assert out["accepted"] is True

    # Drop every reference a caller could hold and force a collection.
    del t, out
    gc.collect()

    await asyncio.wait_for(done.wait(), timeout=5)
    assert len(seen) == 1, "the dispatched task was garbage-collected mid-run"


async def test_the_inflight_set_releases_after_completion():
    from harness import dispatch as mod

    before = len(mod._INFLIGHT)
    task = run_and_notify(
        asyncio.sleep(0, result=env()), task_id=uuid.uuid4(), on_complete=lambda e: None
    )
    assert len(mod._INFLIGHT) == before + 1, "the task was not held"
    await task
    await asyncio.sleep(0)
    assert len(mod._INFLIGHT) == before, "the task was never released (leak)"


# ── TEST 5 · task.exception() non-None ────────────────────────────────────
@pytest.mark.negative
async def test_a_raising_run_task_yields_a_synthesised_failed_envelope(caplog):
    """Unreachable if Doc 05's Rule 6 holds — but it must be loud, not lost."""
    got: list = []
    tid = uuid.uuid4()

    async def explodes():
        raise RuntimeError("run_task violated Rule 6")

    task = run_and_notify(explodes(), task_id=tid, on_complete=got.append)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert len(got) == 1, "the task was lost instead of reported"
    e = got[0]
    assert e.status == "failed", "a raised run must not round up"
    assert e.task_id == tid, "the synthesised envelope must name its task"
    assert "Rule 6" in (e.detail or "")
    assert e.receipts == [], "there is nothing to cite when the run never returned"
    assert any(r.levelname == "ERROR" for r in caplog.records), "the violation was silent"


@pytest.mark.negative
async def test_a_cancelled_run_is_reported_as_failed_not_dropped():
    got: list = []
    tid = uuid.uuid4()

    async def forever():
        await asyncio.Event().wait()

    task = run_and_notify(forever(), task_id=tid, on_complete=got.append)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert len(got) == 1
    assert got[0].status == "failed"
    assert "cancelled" in (got[0].detail or "")


@pytest.mark.negative
async def test_a_failing_callback_does_not_lose_the_task_or_raise(caplog):
    def bad(e):
        raise RuntimeError("callback exploded")

    task = run_and_notify(
        asyncio.sleep(0, result=env()), task_id=uuid.uuid4(), on_complete=bad
    )
    await task
    await asyncio.sleep(0)
    assert any("completion callback failed" in r.message for r in caplog.records)


# ── TEST 6 · status never rounds up through the callback ──────────────────
@pytest.mark.parametrize(
    "status", ["done", "partial", "failed", "needs_clarification", "needs_review"]
)
async def test_the_callback_forwards_the_status_unchanged(status):
    """The dispatch layer must not improve on what the Workroom reported (Law 2)."""
    got: list = []
    e = env(status=status)
    task = run_and_notify(
        asyncio.sleep(0, result=e), task_id=e.task_id, on_complete=got.append
    )
    await task
    await asyncio.sleep(0)
    assert got[0].status == status
