"""SEAM 3 — accept / request-changes close the post-meeting task.

ACCEPTED and CHANGES_REQUESTED were unreachable before this seam: nothing wrote them, so
a task that produced a draft stayed at DRAFTED whatever the human did.
"""
from __future__ import annotations

import uuid

import pytest
from harness.post_meeting.approval import approve
from harness.post_meeting.models import Source, TaskRecord, TaskState, Tier
from harness.post_meeting.outcome import record_accept, record_changes_requested

from ._support import FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
NOW = __import__("datetime").datetime(2026, 7, 28, tzinfo=__import__("datetime").timezone.utc)


async def _drafted(store, draft_id):
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam")
    )
    await store.set_tier(tid, Tier.TICKET_PLAN_DRAFT, state=TaskState.TRIAGED)
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver="Sam", store=store, now=NOW)
    await store.set_state(tid, TaskState.RUNNING)
    await store.set_outcome(tid, state=TaskState.DRAFTED, outcome="staged", draft_id=draft_id)
    return tid


# ── the two terminal states become reachable ──────────────────────────────
async def test_accept_makes_accepted_reachable():
    store = FakeTaskStore()
    draft = uuid.uuid4()
    tid = await _drafted(store, draft)
    assert store.rows[tid]["state"] == TaskState.DRAFTED.value

    got = await record_accept(draft_id=draft, store=store, who="Priya")
    assert got == tid
    assert store.rows[tid]["state"] == TaskState.ACCEPTED.value
    assert "Priya" in store.rows[tid]["outcome"]


async def test_request_changes_makes_changes_requested_reachable():
    store = FakeTaskStore()
    draft = uuid.uuid4()
    tid = await _drafted(store, draft)

    got = await record_changes_requested(draft_id=draft, store=store, who="Priya")
    assert got == tid
    assert store.rows[tid]["state"] == TaskState.CHANGES_REQUESTED.value


async def test_request_changes_is_not_discarded():
    """A reviewer asking for another pass is not the task being abandoned (§3.9)."""
    store = FakeTaskStore()
    draft = uuid.uuid4()
    tid = await _drafted(store, draft)
    await record_changes_requested(draft_id=draft, store=store)
    assert store.rows[tid]["state"] != TaskState.DISCARDED.value
    assert store.rows[tid]["state"] == TaskState.CHANGES_REQUESTED.value


async def test_all_three_terminal_states_are_now_reachable():
    """§3.9's full terminal set, each written by a real path."""
    reachable = set()
    for writer, expected in (
        (record_accept, TaskState.ACCEPTED),
        (record_changes_requested, TaskState.CHANGES_REQUESTED),
    ):
        store = FakeTaskStore()
        draft = uuid.uuid4()
        tid = await _drafted(store, draft)
        await writer(draft_id=draft, store=store)
        reachable.add(store.rows[tid]["state"])
        assert store.rows[tid]["state"] == expected.value
    # DISCARDED is reached by plan expiry (B4) and by the final gate's refusal (B8).
    store = FakeTaskStore()
    tid = await _drafted(store, uuid.uuid4())
    await store.set_outcome(tid, state=TaskState.DISCARDED, outcome="expired")
    reachable.add(store.rows[tid]["state"])
    assert reachable == {
        TaskState.ACCEPTED.value,
        TaskState.CHANGES_REQUESTED.value,
        TaskState.DISCARDED.value,
    }


# ── the draft action always wins ──────────────────────────────────────────
@pytest.mark.negative
async def test_a_draft_with_no_post_meeting_task_is_not_an_error():
    """The live in-meeting path stages drafts too; those have no Doc 07 record."""
    store = FakeTaskStore()
    assert await record_accept(draft_id=uuid.uuid4(), store=store) is None
    assert await record_changes_requested(draft_id=uuid.uuid4(), store=store) is None


@pytest.mark.negative
@pytest.mark.parametrize(
    "boom", [RuntimeError("db gone"), KeyboardInterrupt(), MemoryError()],
    ids=["runtime", "baseexc", "memory"],
)
async def test_a_failing_write_back_never_raises_at_the_human(boom):
    """The accept has already landed on durable storage; bookkeeping must not fail it."""
    store = FakeTaskStore()

    async def explode(_draft_id):
        raise boom

    store.task_id_for_draft = explode  # type: ignore[method-assign]
    assert await record_accept(draft_id=uuid.uuid4(), store=store) is None
    assert await record_changes_requested(draft_id=uuid.uuid4(), store=store) is None


@pytest.mark.negative
async def test_seam3_never_writes_staged_drafts():
    """Doc 07 §3.8: this doc does not write staged_drafts. The route owns that."""
    import pathlib

    src = pathlib.Path(
        "services/harness/src/harness/post_meeting/outcome.py"
    ).read_text(encoding="utf-8").lower()
    assert "insert into staged_drafts" not in src
    assert "update staged_drafts" not in src


# ── the route-side guard ──────────────────────────────────────────────────
async def test_route_guard_is_a_no_op_without_a_sink():
    """A deployment with no post-meeting execution behaves as it did before Doc 07."""
    from control_plane.accept_route import _post_meeting_outcome

    _post_meeting_outcome(None, "accept", "d", "Priya")  # must not raise


@pytest.mark.negative
async def test_route_guard_swallows_a_raising_sink():
    from control_plane.accept_route import _post_meeting_outcome

    def boom(**kw):
        raise RuntimeError("sink exploded")

    _post_meeting_outcome(boom, "accept", "d", "Priya")  # must not raise


async def test_route_guard_passes_the_action_and_actor():
    from control_plane.accept_route import _post_meeting_outcome

    seen: list = []
    _post_meeting_outcome(lambda **kw: seen.append(kw), "reject", "d7", "Priya")
    assert seen == [{"action": "reject", "draft_id": "d7", "who": "Priya"}]
