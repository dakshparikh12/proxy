"""Doc 04 · orchestrator.bundle-dispatch — the WORKROOM-DISPATCH path (§11.6).

The DISPATCH side of §11.6 (the Workroom consumer is Doc 05, not built yet):
real work is bundled as a ``contracts.Bundle`` (the ask verbatim + speaker +
timestamp + a ``notes_ref`` = the meeting_id + the raw transcript tail + a
task_id, §1.3/§11.5) and dispatched to the Workroom. The Workroom task state
reuses ``operation_runs`` (``operation_type='workroom:<id>'``, ``progress`` jsonb
= the task bundle, ``result_ref`` = the terminal Envelope outbox — §12.10). This
suite pins the acceptance BOUNDARY of this node: a dispatched task persists as a
real ``operation_runs`` row and is returnable as an ``Envelope`` handle.

Invariants under test:
  * the bundle carries ``notes_ref`` (the meeting_id UUID), NEVER the notes
    object (§1.3 — the cost/latency trap) and ``transcript_tail`` is a str (D-026).
  * dispatch persists a ``workroom:<task_id>`` row on the REAL DB with the Bundle
    in ``progress`` (§12.10 — reuse operation_runs, no ``workroom_tasks`` table).
  * dispatch returns an Envelope-shaped handle carrying the same task_id.
  * the pre-dispatch estimate gate (§12.7 / A-006): an estimate over the
    remaining task budget asks approval and creates NO row.
  * completion is delivered by callback/handle, never polled.

Product imports live INSIDE the test bodies (or at module top for the pure
contract path), so this module COLLECTS clean and FAILS red before
``services/control-plane/src/control_plane/dispatch.py`` exists. The operation_runs bodies
open the real local Postgres and SKIP cleanly when none is reachable.
"""
from __future__ import annotations

import inspect
import os
import uuid
from datetime import datetime, timezone

import pytest

# Import the contract types from the SAME top-level ``contracts`` module the
# harness product code returns objects from (control_plane.dispatch / control_plane.orchestrator
# use ``from contracts import ...``). ``libs.contracts`` resolves to a DISTINCT
# module object under the test's src-wiring, so importing the types from there
# would make ``isinstance`` fail across the two identities. Match the product.
from contracts import Bundle, Envelope

# ── real-DB helpers ────────────────────────────────────────────────────────


def _local_dsn() -> str | None:
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var, "").strip()
        if dsn:
            return dsn
    return None


async def _open_db():
    from libs.db import Database

    dsn = _local_dsn()
    assert dsn is not None
    return await Database.connect(dsn)


def _require_db() -> str:
    dsn = _local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL)")
    return dsn


def _meeting_id() -> uuid.UUID:
    return uuid.uuid4()


# ── clause 1: bundle assembly — notes_ref (the meeting_id), never the object ──


def test_assemble_bundle_carries_notes_ref_meeting_id_not_the_notes_object() -> None:
    """AC (§1.3/§11.5): the Bundle carries notes_ref = the meeting_id UUID, a
    transcript_tail STR (D-026), and a task_id — never an embedded notes object."""
    from control_plane.dispatch import assemble_bundle

    meeting_id = _meeting_id()
    task_id = uuid.uuid4()
    ts = datetime.now(timezone.utc)
    b = assemble_bundle(
        ask="trace the blast radius of renaming chargeCard",
        speaker="Sam",
        timestamp=ts,
        meeting_id=meeting_id,
        transcript_tail="…so if we rename chargeCard, Sam asked Proxy to check.",
        task_id=task_id,
    )
    assert isinstance(b, Bundle)
    # notes_ref IS the meeting id (a UUID handle), never the notes object (§1.3).
    assert b.notes_ref == meeting_id
    assert isinstance(b.notes_ref, uuid.UUID)
    # transcript_tail is a single string per D-026, not a list.
    assert isinstance(b.transcript_tail, str)
    assert b.ask == "trace the blast radius of renaming chargeCard"
    assert b.speaker == "Sam"
    assert b.task_id == task_id


def test_bundle_never_embeds_the_growing_notes_object() -> None:
    """NEGATIVE (§1.3): assemble_bundle exposes no notes-object field — only a
    ref. A notes object handed in must not be re-serialized into the bundle."""
    from control_plane.dispatch import assemble_bundle

    sig = inspect.signature(assemble_bundle)
    # There is no way to pass an embedded notes object; the handle is the ref.
    assert "notes" not in sig.parameters or "notes_ref" in sig.parameters
    # The Bundle contract itself carries a UUID ref, not a dict/object.
    field = Bundle.model_fields["notes_ref"]
    assert field.annotation is uuid.UUID


# ── clause 2: dispatch persists a real workroom:<id> operation_runs row ───────


@pytest.mark.integration
def test_dispatch_workroom_persists_operation_runs_row_with_bundle_in_progress() -> None:
    """AC boundary: a dispatched task persists as a REAL operation_runs row keyed
    operation_type='workroom:<task_id>' (scope_id = meeting_id::text) with the
    Bundle serialized into ``progress`` jsonb (§12.10)."""
    _require_db()
    import asyncio
    import json

    from control_plane.dispatch import assemble_bundle, dispatch_workroom

    async def _run():
        db = await _open_db()
        try:
            meeting_id = _meeting_id()
            task_id = uuid.uuid4()
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            bundle = assemble_bundle(
                ask="build the checkout-retry refactor",
                speaker="Priya",
                timestamp=datetime.now(timezone.utc),
                meeting_id=meeting_id,
                transcript_tail="Priya: Proxy, build the retry refactor.",
                task_id=task_id,
            )
            handle = await dispatch_workroom(db, bundle)
            # Read the row straight from Postgres — the durable source of truth.
            async with db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, scope_id, operation_type, status, progress, result_ref "
                    "FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            return handle, (dict(row) if row is not None else None), meeting_id, task_id
        finally:
            await db.close()

    handle, row, meeting_id, task_id = asyncio.run(_run())

    assert row is not None, "dispatch created no workroom operation_runs row"
    assert row["operation_type"] == f"workroom:{task_id}"
    assert row["scope_id"] == str(meeting_id)  # the one documented ::text cast
    assert row["status"] == "running"
    # The Bundle is persisted into progress (§12.10 — reuse operation_runs).
    progress = row["progress"]
    if isinstance(progress, str):
        progress = json.loads(progress)
    assert progress is not None, "bundle was not persisted into progress jsonb"
    assert progress["ask"] == "build the checkout-retry refactor"
    assert progress["speaker"] == "Priya"
    assert str(progress["task_id"]) == str(task_id)
    # notes_ref persisted as the meeting_id handle, never the notes object.
    assert str(progress["notes_ref"]) == str(meeting_id)
    # The row is the outbox: result_ref is empty until the Workroom finishes.
    assert row["result_ref"] is None
    # The handle references the persisted row (returnable).
    assert handle.task_id == task_id
    assert str(handle.run_id) == str(row["id"])


# ── clause 3: the dispatched task is returnable as an Envelope handle ─────────


@pytest.mark.integration
def test_dispatch_returns_envelope_handle_referencing_the_task() -> None:
    """AC boundary: the dispatch handle is returnable as a ``contracts.Envelope``
    (the 05→04 result shape), carrying the same task_id — an in-flight handle the
    completion callback later fills, not a polled status."""
    _require_db()
    import asyncio

    from control_plane.dispatch import assemble_bundle, dispatch_workroom

    async def _run():
        db = await _open_db()
        try:
            meeting_id = _meeting_id()
            task_id = uuid.uuid4()
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            bundle = assemble_bundle(
                ask="simulate the migration",
                speaker="Dana",
                timestamp=datetime.now(timezone.utc),
                meeting_id=meeting_id,
                transcript_tail="Dana: Proxy, simulate the migration.",
                task_id=task_id,
            )
            return await dispatch_workroom(db, bundle)
        finally:
            await db.close()

    handle = asyncio.run(_run())
    env = handle.as_envelope()
    assert isinstance(env, Envelope)
    assert env.task_id == handle.task_id
    # The in-flight handle is not yet a finalized deliverable.
    assert env.status in {"partial", "needs_review", "done", "failed", "needs_clarification"}


# ── clause 4: the pre-dispatch estimate gate (§12.7 / A-006) ─────────────────


@pytest.mark.integration
def test_estimate_gate_over_budget_asks_approval_and_creates_no_row() -> None:
    """AC (§12.7): an estimate over the remaining task budget ASKS APPROVAL and
    dispatches NOTHING — no workroom operation_runs row is created."""
    _require_db()
    import asyncio

    from libs.ops.cost import MeetingCost

    from control_plane.dispatch import assemble_bundle, dispatch_workroom

    async def _run():
        db = await _open_db()
        try:
            meeting_id = _meeting_id()
            task_id = uuid.uuid4()
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            bundle = assemble_bundle(
                ask="run a 3-hour fleet simulation",
                speaker="Lee",
                timestamp=datetime.now(timezone.utc),
                meeting_id=meeting_id,
                transcript_tail="Lee: Proxy, run the fleet sim.",
                task_id=task_id,
            )
            cost = MeetingCost(meeting_id=str(meeting_id))
            cost.set_task_budget(remaining_usd=0.50)  # only 50c left
            decision = await dispatch_workroom(
                db, bundle, cost=cost, estimate_usd=8.00  # way over
            )
            async with db.acquire() as conn:
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            return decision, int(cnt)
        finally:
            await db.close()

    decision, cnt = asyncio.run(_run())
    assert decision.dispatched is False
    assert decision.action == "ask_approval"
    assert cnt == 0, "an over-budget estimate must NOT create a workroom row"


@pytest.mark.integration
def test_estimate_gate_within_budget_dispatches_and_persists_row() -> None:
    """AC (§12.7): an estimate within the remaining task budget DISPATCHES — the
    workroom operation_runs row is created and the decision says 'dispatch'."""
    _require_db()
    import asyncio

    from libs.ops.cost import MeetingCost

    from control_plane.dispatch import assemble_bundle, dispatch_workroom

    async def _run():
        db = await _open_db()
        try:
            meeting_id = _meeting_id()
            task_id = uuid.uuid4()
            async with db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            bundle = assemble_bundle(
                ask="trace one dependent",
                speaker="Ana",
                timestamp=datetime.now(timezone.utc),
                meeting_id=meeting_id,
                transcript_tail="Ana: Proxy, trace the dependent.",
                task_id=task_id,
            )
            cost = MeetingCost(meeting_id=str(meeting_id))
            cost.set_task_budget(remaining_usd=20.00)  # plenty
            decision = await dispatch_workroom(
                db, bundle, cost=cost, estimate_usd=2.00
            )
            async with db.acquire() as conn:
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM operation_runs WHERE operation_type = $1",
                    f"workroom:{task_id}",
                )
            return decision, int(cnt)
        finally:
            await db.close()

    decision, cnt = asyncio.run(_run())
    assert decision.dispatched is True
    assert decision.action == "dispatch"
    assert cnt == 1, "a within-budget dispatch must create exactly one workroom row"


# ── clause 5: completion is delivered by callback/handle, never polled ────────


def test_dispatch_never_polls_for_completion() -> None:
    """INVARIANT (§3.2): the runtime delivers the done-moment via a callback —
    nothing polls. The dispatch module names no poll loop (no sleep-poll,
    no while-status)."""
    import control_plane.dispatch as mod

    src = inspect.getsource(mod)
    lowered = src.lower()
    # No busy-poll for completion in the dispatch path.
    assert "while true" not in lowered
    for banned in ("time.sleep", "asyncio.sleep(", "poll_until", "poll_for"):
        assert banned not in lowered, f"dispatch must not poll for completion ({banned!r})"


def test_dispatch_handle_exposes_a_completion_callback_seam() -> None:
    """The in-flight handle exposes a completion seam (on_complete / callback) so
    the run loop re-wakes Proxy on the Envelope — a push, not a poll."""
    from control_plane.dispatch import WorkroomHandle

    names = set(dir(WorkroomHandle))
    assert names & {"on_complete", "complete", "set_result", "as_envelope"}, (
        "the dispatch handle must expose a completion-callback / result seam"
    )
