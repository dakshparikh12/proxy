"""B5 — the approval gate. Criteria: AC-PME-07, AC-PME-07-NEG.

This is Doc 07's P0 safety boundary, so the doubles here are adversarial by construction:
``ForbiddenSandbox`` raises if it is ever reached, and ``FakeTaskStore`` re-implements
migration 0009's CHECK and trigger. A permissive stub in either place would make these
criteria pass while the product violated them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from control_plane.post_meeting.approval import (
    PRE_APPROVAL_MODEL_CALLS,
    PRE_APPROVAL_WRITABLE_TABLES,
    Approval,
    ApprovalRefused,
    approve,
    is_approved,
    is_named_human,
    load_and_check,
    may_dispatch,
)
from control_plane.post_meeting.clarify import run_clarify
from control_plane.post_meeting.models import UNRESOLVED, Source, TaskRecord, TaskState

from ._support import FakeClarifyStore, FakeTaskStore, ForbiddenSandbox

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


async def _seed(store, state=TaskState.PLANNED, owner="Sam"):
    tid = await store.insert_task(
        TaskRecord(
            task_id=None, tenant_id=TENANT, meeting_id=MEETING,
            source=Source.CLOSE_ITEM, item_ref="m#0", owner=owner,
        )
    )
    if state is not TaskState.EXTRACTED:
        await store.set_state(tid, state)
    return tid


# ── AC-PME-07 · nothing runs, nothing durable, before approval ────────────
async def test_ac_pme_07_no_sandbox_starts_before_approval():
    sandbox = ForbiddenSandbox()
    store = FakeTaskStore()
    tid = await _seed(store)
    row = await store.get(tid)
    assert may_dispatch(row) is False
    assert sandbox.call_count == 0


async def test_ac_pme_07_running_cannot_be_entered_from_any_pre_approval_state():
    for state in (
        TaskState.EXTRACTED, TaskState.TRIAGED, TaskState.CLARIFYING, TaskState.PLANNED,
    ):
        store = FakeTaskStore()
        tid = await _seed(store, state)
        with pytest.raises(ValueError, match="RUNNING may only be entered from APPROVED"):
            await store.set_state(tid, TaskState.RUNNING)
        assert store.rows[tid]["state"] == state.value


async def test_ac_pme_07_running_is_reachable_only_after_a_real_approval():
    store = FakeTaskStore()
    tid = await _seed(store)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)
    assert store.rows[tid]["state"] == TaskState.RUNNING.value


async def test_ac_pme_07_approved_requires_both_approver_fields():
    """The database rejects APPROVED without approved_by AND approved_at."""
    store = FakeTaskStore()
    tid = await _seed(store)
    with pytest.raises(ValueError, match="approved_needs_approver"):
        await store.set_state(tid, TaskState.APPROVED)  # state only, no approver
    assert store.rows[tid]["state"] == TaskState.PLANNED.value


async def test_ac_pme_07_approval_writes_state_and_approver_in_one_step():
    store = FakeTaskStore()
    tid = await _seed(store)
    res = await approve(task_id=tid, approver="  Sam  ", store=store, now=NOW)
    assert isinstance(res, Approval)
    row = store.rows[tid]
    assert row["state"] == TaskState.APPROVED.value
    assert row["approved_by"] == "Sam"
    assert row["approved_at"] == NOW
    assert is_approved(row) is True


async def test_ac_pme_07_pre_approval_write_set_is_closed():
    """Exactly {post_meeting_tasks, clarify_items}; a third table fails this."""
    assert PRE_APPROVAL_WRITABLE_TABLES == frozenset(
        {"post_meeting_tasks", "clarify_items"}
    )
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, TaskState.EXTRACTED, owner=UNRESOLVED)
    await run_clarify(
        [{"task_id": tid, "item_ref": "m#0", "owner": UNRESOLVED, "text": "x",
          "has_scope": False, "has_done_condition": False}],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=(),
    )
    written = ts.tables_written | cs.tables_written
    assert written <= PRE_APPROVAL_WRITABLE_TABLES
    assert "staged_drafts" not in written
    assert "operation_runs" not in written
    assert "meeting_cost" not in written


async def test_ac_pme_07_model_work_before_approval_is_bounded_to_triage_and_plan():
    assert PRE_APPROVAL_MODEL_CALLS == frozenset({"triage", "plan"})


# ── AC-PME-07-NEG · the gate holds under error and ambiguity ──────────────
@pytest.mark.negative
async def test_ac_pme_07_neg_approved_with_null_approver_is_not_approved():
    """A row claiming APPROVED with a missing approver field is NOT an approval."""
    for row in (
        {"state": "APPROVED", "approved_by": None, "approved_at": NOW},
        {"state": "APPROVED", "approved_by": "Sam", "approved_at": None},
        {"state": "APPROVED", "approved_by": "", "approved_at": NOW},
        {"state": "APPROVED", "approved_by": "   ", "approved_at": NOW},
        {"state": "APPROVED"},
    ):
        assert is_approved(row) is False
        assert may_dispatch(row) is False


@pytest.mark.negative
async def test_ac_pme_07_neg_unresolved_can_never_approve():
    assert is_named_human(UNRESOLVED) is False
    assert is_named_human("unresolved") is False
    assert is_named_human("SYSTEM") is False
    assert is_named_human("Proxy") is False
    assert is_named_human("Sam") is True

    store = FakeTaskStore()
    tid = await _seed(store)
    for bad in (UNRESOLVED, "", "   ", None, 42, "SYSTEM"):
        with pytest.raises(ApprovalRefused):
            await approve(task_id=tid, approver=bad, store=store, now=NOW)
    assert store.rows[tid]["state"] == TaskState.PLANNED.value


@pytest.mark.negative
async def test_ac_pme_07_neg_lookup_failure_fails_closed():
    class Broken:
        async def get(self, task_id):
            raise ConnectionRefusedError("postgres refused")

    ok, err = await load_and_check(task_id=uuid.uuid4(), store=Broken())
    assert ok is False, "the gate opened on a substrate error"
    assert isinstance(err, ConnectionRefusedError), "the ambiguity must be reported"


@pytest.mark.negative
async def test_ac_pme_07_neg_unreadable_row_shapes_fail_closed():
    for row in (None, [], "APPROVED", 0, {"state": None}, {"state": object()}):
        assert is_approved(row) is False
        assert may_dispatch(row) is False


@pytest.mark.negative
async def test_ac_pme_07_neg_no_sandbox_on_an_ambiguous_approval():
    sandbox = ForbiddenSandbox()
    ambiguous = {"state": "APPROVED", "approved_by": None, "approved_at": None}
    if may_dispatch(ambiguous):
        await sandbox.start()
    assert sandbox.call_count == 0


@pytest.mark.negative
async def test_ac_pme_07_neg_approval_only_from_planned():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.CLARIFYING)
    with pytest.raises(ApprovalRefused, match="only be approved from PLANNED"):
        await approve(
            task_id=tid, approver="Sam", store=store, now=NOW,
            current_state=TaskState.CLARIFYING,
        )
    assert store.rows[tid]["state"] == TaskState.CLARIFYING.value


@pytest.mark.negative
async def test_ac_pme_07_neg_running_from_discarded_is_rejected():
    store = FakeTaskStore()
    tid = await _seed(store)
    await store.set_outcome(tid, state=TaskState.DISCARDED, outcome="expired")
    with pytest.raises(ValueError, match="RUNNING may only be entered from APPROVED"):
        await store.set_state(tid, TaskState.RUNNING)


@pytest.mark.negative
async def test_ac_pme_07_neg_a_row_cannot_be_born_running():
    """The INSERT arm of the trigger — a BEFORE UPDATE guard alone would miss this."""
    store = FakeTaskStore()
    with pytest.raises(ValueError, match="cannot INSERT a row directly at RUNNING"):
        await store.insert_task(
            TaskRecord(
                task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                source=Source.CLOSE_ITEM, item_ref="m#0", state=TaskState.RUNNING,
            )
        )


@pytest.mark.negative
async def test_ac_pme_07_neg_updates_to_an_already_running_row_are_allowed():
    """The trigger gates the TRANSITION, not every write to a running row.

    Without the ``OLD.state IS DISTINCT FROM 'RUNNING'`` clause, writing cost or outcome
    onto a running task would compare 'RUNNING' <> 'APPROVED' and raise — deadlocking
    every task on its first progress write.
    """
    store = FakeTaskStore()
    tid = await _seed(store)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)
    await store.set_outcome(tid, state=TaskState.RUNNING, outcome="still going", cost_usd=0.4)
    assert store.rows[tid]["state"] == TaskState.RUNNING.value
    assert store.rows[tid]["cost_usd"] == 0.4
