"""B4 — plan. Criteria: AC-PME-08, AC-PME-08-NEG."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from control_plane.post_meeting.config import PostMeetingConfig
from control_plane.post_meeting.models import Source, TaskRecord, TaskState
from control_plane.post_meeting.plan import (
    PLAN_SCHEMA,
    expire_stale_plans,
    is_expired,
    render_plan,
    run_plan,
)

from libs.llm.src.llm.structured import StructuredOutputError, StructuredResult

from ._support import FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
CFG = PostMeetingConfig(plan_expiry_hours=48)
T0 = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

PLAN_DATA = {
    "task_one_line": "raise the checkout retry ceiling to 5",
    "why_it_exists": "Sam took it in the meeting",
    "meeting_reference": "m#0",
    "owner": "Sam",
    "assumptions": ["the existing retry test covers the path"],
    "risks": ["a higher ceiling lengthens the worst-case checkout"],
    "done_looks_like": "the retry test passes at the new ceiling",
    "files_expected": ["payments/checkout/retry.py"],
    "steps": ["bump the constant", "run the retry test"],
    "confidence": "high",
}


async def passthrough(op, *, service, **kwargs):
    return await op()


def caller_ok(data=None):
    async def _c(*, model, prompt, output_schema, tool_name):
        assert output_schema is PLAN_SCHEMA
        return StructuredResult(data=data or PLAN_DATA, total_cost_usd=0.02)

    return _c


def caller_raising(exc):
    async def _c(*, model, prompt, output_schema, tool_name):
        raise exc

    return _c


async def _seed(store, state=TaskState.TRIAGED):
    tid = await store.insert_task(
        TaskRecord(
            task_id=None, tenant_id=TENANT, meeting_id=MEETING,
            source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam",
        )
    )
    if state is not TaskState.EXTRACTED:
        await store.set_state(tid, state)
    return tid


# ── plan authoring ────────────────────────────────────────────────────────
async def test_plan_is_written_to_the_task_record_and_moves_to_planned():
    store = FakeTaskStore()
    tid = await _seed(store)
    res = await run_plan(
        task_id=tid, text="bump retry", owner="Sam", item_ref="m#0",
        store=store, caller=caller_ok(), call_external=passthrough,
    )
    assert res.ok
    assert store.rows[tid]["state"] == TaskState.PLANNED.value
    assert store.rows[tid]["plan"] == res.plan_text
    assert store.tables_written == {"post_meeting_tasks"}, (
        "the plan text must live on the task's own record and nowhere else (§3.4)"
    )


async def test_plan_carries_all_nine_spec_fields():
    text = render_plan(PLAN_DATA)
    for token in (
        "TASK:", "WHY:", "m#0", "OWNER:", "DONE WHEN:", "CONFIDENCE:",
        "ASSUMPTIONS:", "RISKS:", "FILES EXPECTED:", "STEPS:",
    ):
        assert token in text, f"plan missing {token}"


async def test_failed_plan_call_does_not_move_the_task_to_planned():
    store = FakeTaskStore()
    tid = await _seed(store)
    res = await run_plan(
        task_id=tid, text="x", owner="Sam", item_ref="m#0",
        store=store, caller=caller_raising(StructuredOutputError("5xx")),
        call_external=passthrough,
    )
    assert res.ok is False
    assert store.rows[tid]["state"] == TaskState.TRIAGED.value
    assert store.rows[tid]["plan"] is None


async def test_planning_starts_no_sandbox():
    """Static: the planning module has no sandbox to reach (§3.4).

    A ForbiddenSandbox the module never receives would read 0 regardless of behaviour, so
    the assertion is on the source: no sandbox, no E2B, no propose_change in B4.
    """
    from ._support import assert_no_code_reference

    assert_no_code_reference(
        "services/control-plane/src/control_plane/post_meeting/plan.py",
        ("sandbox", "e2b", "propose_change", "staged_drafts"),
    )

    store = FakeTaskStore()
    tid = await _seed(store)
    res = await run_plan(
        task_id=tid, text="x", owner="Sam", item_ref="m#0",
        store=store, caller=caller_ok(), call_external=passthrough,
    )
    assert res.ok


# ── AC-PME-08 · an unanswered plan expires quietly ────────────────────────
async def test_ac_pme_08_expired_plan_closes_without_proceeding():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    rows = [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}]

    res = await expire_stale_plans(
        rows, store=store, now=T0 + timedelta(hours=49), config=CFG
    )
    assert res.expired == [tid]
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value
    assert store.rows[tid]["state"] != TaskState.APPROVED.value
    assert store.rows[tid]["state"] != TaskState.RUNNING.value
    assert "expired" in store.rows[tid]["outcome"]


async def test_ac_pme_08_expiry_sends_no_notification():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    res = await expire_stale_plans(
        [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}],
        store=store, now=T0 + timedelta(hours=100), config=CFG,
    )
    assert res.notifications_sent == 0, "Proxy does not nag (§3.4)"


async def test_ac_pme_08_plan_within_the_window_is_untouched():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    res = await expire_stale_plans(
        [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}],
        store=store, now=T0 + timedelta(hours=47), config=CFG,
    )
    assert res.expired == []
    assert store.rows[tid]["state"] == TaskState.PLANNED.value


async def test_ac_pme_08_expiry_boundary_is_inclusive_at_the_configured_hours():
    assert is_expired(planned_at=T0, now=T0 + timedelta(hours=48), plan_expiry_hours=48)
    assert not is_expired(
        planned_at=T0, now=T0 + timedelta(hours=47, minutes=59), plan_expiry_hours=48
    )


async def test_ac_pme_08_no_sandbox_is_provisioned_by_expiry():
    """Expiry closes a task; it never provisions anything.

    Behavioural half: the only table touched is post_meeting_tasks, and the task lands
    terminal. Structural half is covered by test_planning_starts_no_sandbox, which asserts
    the module has no sandbox reference to reach at all.
    """
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    await expire_stale_plans(
        [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}],
        store=store, now=T0 + timedelta(hours=72), config=CFG,
    )
    assert store.tables_written == {"post_meeting_tasks"}
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value


# ── AC-PME-08-NEG · clock/sweep faults never approve ──────────────────────
@pytest.mark.negative
async def test_ac_pme_08_neg_backward_clock_does_not_unexpire():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    rows = [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}]

    await expire_stale_plans(rows, store=store, now=T0 + timedelta(hours=72), config=CFG)
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value

    # Clock jumps backward and the sweep runs again over the now-terminal row.
    rows2 = [{"task_id": tid, "state": TaskState.DISCARDED, "planned_at": T0}]
    res = await expire_stale_plans(
        rows2, store=store, now=T0 - timedelta(hours=5), config=CFG
    )
    assert res.expired == []
    assert store.rows[tid]["state"] == TaskState.DISCARDED.value, "a terminal task reopened"
    assert not is_expired(planned_at=T0, now=T0 - timedelta(hours=5), plan_expiry_hours=48)


@pytest.mark.negative
async def test_ac_pme_08_neg_rerun_after_crash_does_not_double_close_or_renotify():
    store = FakeTaskStore()
    t1 = await _seed(store, TaskState.PLANNED)
    t2 = await _seed(store, TaskState.PLANNED)
    rows = [
        {"task_id": t1, "state": TaskState.PLANNED, "planned_at": T0},
        {"task_id": t2, "state": TaskState.PLANNED, "planned_at": T0},
    ]
    now = T0 + timedelta(hours=72)

    first = await expire_stale_plans(rows[:1], store=store, now=now, config=CFG)  # "crash"
    assert first.expired == [t1]

    # Re-run over the FULL set, as a restarted sweep would see it.
    rerun = [
        {"task_id": t1, "state": TaskState.DISCARDED, "planned_at": T0},
        {"task_id": t2, "state": TaskState.PLANNED, "planned_at": T0},
    ]
    second = await expire_stale_plans(rerun, store=store, now=now, config=CFG)
    assert second.expired == [t2], "the already-closed task was closed twice"
    assert t1 in second.skipped
    assert second.notifications_sent == 0


@pytest.mark.negative
async def test_ac_pme_08_neg_sweep_never_approves_or_runs_a_task():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.PLANNED)
    await expire_stale_plans(
        [{"task_id": tid, "state": TaskState.PLANNED, "planned_at": T0}],
        store=store, now=T0 + timedelta(hours=99), config=CFG,
    )
    assert store.rows[tid]["approved_by"] is None
    assert store.rows[tid]["approved_at"] is None
    assert store.rows[tid]["state"] not in {
        TaskState.APPROVED.value, TaskState.RUNNING.value
    }


@pytest.mark.negative
async def test_ac_pme_08_neg_one_failing_row_does_not_abort_the_sweep():
    store = FakeTaskStore()
    t1 = await _seed(store, TaskState.PLANNED)
    t2 = await _seed(store, TaskState.PLANNED)
    real = store.set_outcome
    calls = {"n": 0}

    async def flaky(task_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("dropped")
        return await real(task_id, **kw)

    store.set_outcome = flaky  # type: ignore[method-assign]
    res = await expire_stale_plans(
        [
            {"task_id": t1, "state": TaskState.PLANNED, "planned_at": T0},
            {"task_id": t2, "state": TaskState.PLANNED, "planned_at": T0},
        ],
        store=store, now=T0 + timedelta(hours=72), config=CFG,
    )
    assert res.expired == [t2]
    assert len(res.errors) == 1


@pytest.mark.negative
async def test_ac_pme_08_neg_non_planned_states_are_never_expired():
    store = FakeTaskStore()
    tid = await _seed(store, TaskState.CLARIFYING)
    res = await expire_stale_plans(
        [{"task_id": tid, "state": TaskState.CLARIFYING, "planned_at": T0}],
        store=store, now=T0 + timedelta(hours=999), config=CFG,
    )
    assert res.expired == []
    assert store.rows[tid]["state"] == TaskState.CLARIFYING.value
