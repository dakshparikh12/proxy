"""§3.4 owner actions: reject, downgrade-to-ticket, edit. (split is deferred.)"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from harness.post_meeting.models import UNRESOLVED, Source, TaskRecord, TaskState, Tier
from harness.post_meeting.owner_actions import (
    OwnerActionRefused,
    downgrade_to_ticket,
    edit_plan,
    reject,
)
from harness.post_meeting.plan import expire_stale_plans

from ._support import FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT, MEETING = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


async def _planned(store, tier=Tier.TICKET_PLAN_DRAFT):
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam")
    )
    await store.set_tier(tid, tier, state=TaskState.TRIAGED)
    await store.set_plan(tid, "PLAN: bump the retry ceiling", state=TaskState.PLANNED)
    return tid


# ── reject ────────────────────────────────────────────────────────────────
async def test_reject_discards_the_task_and_names_the_rejector():
    store = FakeTaskStore()
    tid = await _planned(store)
    act = await reject(
        task_id=tid, actor="Priya", current_state=TaskState.PLANNED, store=store
    )
    assert act.new_state is TaskState.DISCARDED
    row = store.rows[tid]
    assert row["state"] == TaskState.DISCARDED.value
    assert "Priya" in row["outcome"] and "rejected" in row["outcome"]


async def test_reject_is_distinct_from_changes_requested():
    """CHANGES_REQUESTED is another pass on an existing draft; reject means don't do it."""
    store = FakeTaskStore()
    tid = await _planned(store)
    await reject(task_id=tid, actor="Priya", current_state=TaskState.PLANNED, store=store)
    assert store.rows[tid]["state"] != TaskState.CHANGES_REQUESTED.value


async def test_rejected_task_is_not_dispatchable_and_not_swept():
    store = FakeTaskStore()
    tid = await _planned(store)
    await reject(task_id=tid, actor="Priya", current_state=TaskState.PLANNED, store=store)
    assert await store.count_dispatchable_for_meeting(MEETING) == 0
    assert await store.planned_tasks_for_sweep() == []


# ── downgrade to a ticket ─────────────────────────────────────────────────
async def test_downgrade_drops_the_tier_and_leaves_the_item_alive():
    store = FakeTaskStore()
    tid = await _planned(store)
    act = await downgrade_to_ticket(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED, store=store
    )
    row = store.rows[tid]
    assert act.new_state is TaskState.TRIAGED
    assert row["tier"] == Tier.TICKET.value
    assert row["state"] == TaskState.TRIAGED.value
    assert row["state"] != TaskState.DISCARDED.value, "a downgrade is not a discard"
    assert "downgraded" in row["outcome"]


async def test_downgrade_clears_the_plan_and_the_expiry_clock():
    store = FakeTaskStore()
    tid = await _planned(store)
    assert store.rows[tid]["planned_at"] is not None
    await downgrade_to_ticket(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED, store=store
    )
    assert store.rows[tid]["plan"] is None
    assert store.rows[tid]["planned_at"] is None


async def test_a_downgraded_ticket_is_neither_swept_nor_dispatchable():
    store = FakeTaskStore()
    tid = await _planned(store)
    await downgrade_to_ticket(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED, store=store
    )
    assert await store.planned_tasks_for_sweep() == [], "a ticket has no plan to expire"
    assert await store.count_dispatchable_for_meeting(MEETING) == 0, (
        "ticket is not a dispatchable tier"
    )


# ── edit ──────────────────────────────────────────────────────────────────
async def test_edit_rewrites_the_plan_and_stays_planned():
    store = FakeTaskStore()
    tid = await _planned(store)
    act = await edit_plan(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED,
        new_plan="PLAN: bump to 3, not 5", store=store,
    )
    assert act.new_state is TaskState.PLANNED
    row = store.rows[tid]
    assert row["plan"] == "PLAN: bump to 3, not 5"
    assert row["state"] == TaskState.PLANNED.value


async def test_edit_does_not_grant_approval():
    """The whole point: an edit is not a way around the gate."""
    store = FakeTaskStore()
    tid = await _planned(store)
    await edit_plan(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED,
        new_plan="new plan", store=store,
    )
    row = store.rows[tid]
    assert row["approved_by"] is None
    assert row["approved_at"] is None
    assert row["state"] != TaskState.APPROVED.value
    with pytest.raises(ValueError, match="RUNNING may only be entered from APPROVED"):
        await store.set_state(tid, TaskState.RUNNING)


async def test_edit_restarts_the_expiry_clock():
    """An edited plan is a new plan; it must not inherit the old window's remains."""
    store = FakeTaskStore()
    tid = await _planned(store)
    first = store.rows[tid]["planned_at"]

    await edit_plan(
        task_id=tid, actor="Sam", current_state=TaskState.PLANNED,
        new_plan="revised", store=store,
    )
    second = store.rows[tid]["planned_at"]
    assert second is not None and second >= first

    # And a sweep using the ORIGINAL stamp no longer expires it, because the row's
    # own clock moved forward.
    from datetime import timedelta

    rows = await store.planned_tasks_for_sweep()
    assert rows[0]["planned_at"] == second
    res = await expire_stale_plans(
        rows, store=store, now=second + timedelta(hours=1),
        config=__import__(
            "harness.post_meeting.config", fromlist=["PostMeetingConfig"]
        ).PostMeetingConfig(plan_expiry_hours=48),
    )
    assert res.expired == []


async def test_an_empty_edit_is_refused():
    store = FakeTaskStore()
    tid = await _planned(store)
    for bad in ("", "   ", None, 7):
        with pytest.raises(OwnerActionRefused, match="empty edit"):
            await edit_plan(
                task_id=tid, actor="Sam", current_state=TaskState.PLANNED,
                new_plan=bad, store=store,
            )
    assert store.rows[tid]["plan"] == "PLAN: bump the retry ceiling", "the plan changed"


# ── shared preconditions ──────────────────────────────────────────────────
@pytest.mark.negative
@pytest.mark.parametrize(
    "state",
    ["EXTRACTED", "TRIAGED", "CLARIFYING", "APPROVED", "RUNNING", "DRAFTED", "DISCARDED"],
)
@pytest.mark.parametrize("action", ["reject", "downgrade", "edit"])
async def test_every_owner_action_applies_only_to_a_planned_task(action, state):
    store = FakeTaskStore()
    tid = await _planned(store)
    before = dict(store.rows[tid])

    with pytest.raises(OwnerActionRefused, match="PLANNED"):
        if action == "reject":
            await reject(task_id=tid, actor="Sam", current_state=state, store=store)
        elif action == "downgrade":
            await downgrade_to_ticket(
                task_id=tid, actor="Sam", current_state=state, store=store
            )
        else:
            await edit_plan(
                task_id=tid, actor="Sam", current_state=state,
                new_plan="x", store=store,
            )
    assert store.rows[tid] == before, f"{action} mutated a {state} task"


@pytest.mark.negative
@pytest.mark.parametrize("action", ["reject", "downgrade", "edit"])
async def test_every_owner_action_requires_a_named_human(action):
    store = FakeTaskStore()
    tid = await _planned(store)
    before = dict(store.rows[tid])

    for actor in (UNRESOLVED, "SYSTEM", "Proxy", "", "   ", None, 42):
        with pytest.raises(OwnerActionRefused, match="named human"):
            if action == "reject":
                await reject(
                    task_id=tid, actor=actor, current_state=TaskState.PLANNED, store=store
                )
            elif action == "downgrade":
                await downgrade_to_ticket(
                    task_id=tid, actor=actor, current_state=TaskState.PLANNED, store=store
                )
            else:
                await edit_plan(
                    task_id=tid, actor=actor, current_state=TaskState.PLANNED,
                    new_plan="x", store=store,
                )
    assert store.rows[tid] == before


async def test_split_is_deferred_and_recorded_as_such():
    """split is the one §3.4 action not built. It must be a decision, not an omission."""
    import pathlib

    src = pathlib.Path(
        "services/harness/src/harness/post_meeting/owner_actions.py"
    ).read_text(encoding="utf-8")
    assert "split: DEFERRED" in src
    for reason in ("parent/child", "max_tasks_per_meeting", "1–1.5 days"):
        assert reason in src, f"the deferral must state {reason!r}"

    spec = pathlib.Path(
        "product/v0-spec/07-POST-MEETING-EXECUTION.md"
    ).read_text(encoding="utf-8")
    assert "split" in spec and "DEFERRED" in spec
