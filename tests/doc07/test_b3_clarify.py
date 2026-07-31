"""B3 — clarify. Criteria: AC-PME-03, AC-PME-03-NEG, AC-PME-04, AC-PME-04-NEG."""
from __future__ import annotations

import uuid

import pytest
from control_plane.post_meeting.clarify import assess, route_question, run_clarify
from control_plane.post_meeting.models import UNRESOLVED, Source, TaskRecord, TaskState

from ._support import FakeClarifyStore, FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
CHANNELS = ("draft_card",)


async def _seed(store: FakeTaskStore, **over):
    task = TaskRecord(
        task_id=None,
        tenant_id=TENANT,
        meeting_id=MEETING,
        source=over.pop("source", Source.CLOSE_ITEM),
        item_ref=over.pop("item_ref", "m#0"),
        owner=over.pop("owner", UNRESOLVED),
    )
    tid = await store.insert_task(task)
    return tid


def _item(task_id, **over):
    base = {
        "task_id": task_id,
        "item_ref": "m#0",
        "owner": UNRESOLVED,
        "text": "look at the checkout error spike",
        "has_scope": False,
        "has_done_condition": False,
    }
    base.update(over)
    return base


# ── AC-PME-03 · ambiguous item is never planned, becomes a question ───────
@pytest.mark.parametrize(
    "over,label",
    [
        ({"owner": UNRESOLVED, "has_scope": True, "has_done_condition": True}, "no owner"),
        ({"owner": "Sam", "has_scope": False, "has_done_condition": True}, "no scope"),
        ({"owner": "Sam", "has_scope": True, "has_done_condition": False}, "no done-cond"),
    ],
)
async def test_ac_pme_03_each_missing_signal_raises_a_question(over, label):
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner=over["owner"])
    res = await run_clarify(
        [_item(tid, **over)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert len(cs.rows) == 1, f"{label}: no clarify_items row written"
    assert ts.rows[tid]["state"] == TaskState.CLARIFYING.value
    assert ts.rows[tid]["plan"] is None, f"{label}: an ambiguous item was planned"
    assert res.outcomes[0].written is True


async def test_ac_pme_03_clarify_row_carries_blocking_ref_back_to_the_item():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)
    await run_clarify(
        [_item(tid, item_ref="m#7")],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert cs.rows[0]["blocking_ref"] == "m#7"
    assert cs.rows[0]["question"]


async def test_ac_pme_03_complete_item_raises_no_question():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner="Sam")
    res = await run_clarify(
        [_item(tid, owner="Sam", has_scope=True, has_done_condition=True)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert cs.rows == []
    assert res.outcomes == []
    assert ts.rows[tid]["state"] == TaskState.EXTRACTED.value


async def test_ac_pme_03_unknown_judged_signal_is_treated_as_missing():
    """None means "triage did not say". An unknown scope is not a scope."""
    a = assess(owner="Sam", text="x", has_scope=None, has_done_condition=None)
    assert a.missing_scope and a.missing_done_condition and a.any
    assert assess(owner="Sam", text="x", has_scope=True, has_done_condition=True).any is False


async def test_ac_pme_03_clarify_items_is_the_only_extra_table_written():
    """AC-PME-07's permitted pre-approval write set: {post_meeting_tasks, clarify_items}."""
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)
    await run_clarify(
        [_item(tid)], tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert ts.tables_written | cs.tables_written == {"post_meeting_tasks", "clarify_items"}


# ── AC-PME-03-NEG · a failed clarify write still blocks planning ──────────
@pytest.mark.negative
async def test_ac_pme_03_neg_failed_clarify_write_still_blocks_planning():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    cs.insert_error = ConnectionResetError("clarify insert rejected")
    tid = await _seed(ts)

    res = await run_clarify(
        [_item(tid)], tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    o = res.outcomes[0]
    assert o.written is False
    assert isinstance(o.error, ConnectionResetError), "the failure must be surfaced"
    assert ts.rows[tid]["plan"] is None, "fail-open: an unscoped item became plannable"
    assert ts.rows[tid]["state"] == TaskState.CLARIFYING.value
    assert ts.rows[tid]["state"] != TaskState.APPROVED.value


@pytest.mark.negative
async def test_ac_pme_03_neg_failed_write_is_not_reported_as_asked():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    cs.insert_error = RuntimeError("db down")
    tid = await _seed(ts)
    res = await run_clarify(
        [_item(tid)], tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert res.outcomes[0].written is False
    assert res.outcomes[0].routed_to is None
    assert res.outcomes[0].pending is True, "must be retryable, not silently dropped"


@pytest.mark.negative
async def test_ac_pme_03_neg_item_is_held_even_if_state_write_fails():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)

    async def boom(*a, **k):
        raise ConnectionRefusedError("postgres refused")

    ts.set_state = boom  # type: ignore[method-assign]
    res = await run_clarify(
        [_item(tid)], tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert res.outcomes[0].pending is True
    assert ts.rows[tid]["plan"] is None
    assert cs.rows == [], "no question should be recorded as asked when the hold failed"


# ── AC-PME-04 · no person or no channel ⇒ pending, nothing sent ───────────
async def test_ac_pme_04_no_attributed_person_leaves_it_pending():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)
    res = await run_clarify(
        [_item(tid, owner=UNRESOLVED, attributed_person=None)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    o = res.outcomes[0]
    assert o.routed_to is None
    assert o.pending is True
    assert o.written is True, "the question is still recorded, just not delivered"
    assert ts.rows[tid]["state"] == TaskState.CLARIFYING.value


async def test_ac_pme_04_no_channel_leaves_it_pending():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner="Sam")
    res = await run_clarify(
        [_item(tid, owner="Sam", attributed_person="Sam")],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=(),  # no out-of-meeting channel
    )
    assert res.outcomes[0].routed_to is None
    assert res.outcomes[0].pending is True


async def test_ac_pme_04_with_person_and_channel_it_routes():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner="Sam")
    res = await run_clarify(
        [_item(tid, owner="Sam", attributed_person="Sam", has_scope=True)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    assert res.outcomes[0].routed_to == "Sam"
    assert res.outcomes[0].pending is False


async def test_ac_pme_04_pending_questions_are_listed_for_the_draft_card():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    t1 = await _seed(ts, item_ref="m#0")
    t2 = await _seed(ts, item_ref="m#1")
    res = await run_clarify(
        [_item(t1, item_ref="m#0"), _item(t2, item_ref="m#1")],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=(),
    )
    assert len(res.pending) == 2
    assert len(await cs.pending_for_meeting(MEETING)) == 2


# ── AC-PME-04-NEG · lookup failure sends nothing, invents no channel ──────
@pytest.mark.negative
async def test_ac_pme_04_neg_no_fallback_channel_is_invented():
    """route_question returns None rather than picking a plausible recipient."""
    assert route_question(attributed_person=None, channels=("draft_card",)) is None
    assert route_question(attributed_person="", channels=("draft_card",)) is None
    assert route_question(attributed_person="   ", channels=("draft_card",)) is None
    assert route_question(attributed_person=UNRESOLVED, channels=("draft_card",)) is None
    assert route_question(attributed_person="Sam", channels=()) is None
    assert route_question(attributed_person="Sam", channels=("draft_card",)) == "Sam"


@pytest.mark.negative
async def test_ac_pme_04_neg_malformed_channel_set_sends_nothing():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner="Sam")
    sent: list[str] = []

    res = await run_clarify(
        [_item(tid, owner="Sam", attributed_person="Sam")],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=(),  # lookup produced nothing usable
    )
    assert sent == [], "a message was sent with no usable channel"
    assert res.outcomes[0].pending is True
    assert ts.rows[tid]["state"] == TaskState.CLARIFYING.value


@pytest.mark.negative
async def test_ac_pme_04_neg_pending_item_never_advances_toward_approved():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)
    await run_clarify(
        [_item(tid)], tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=(),
    )
    assert ts.rows[tid]["state"] == TaskState.CLARIFYING.value
    assert ts.rows[tid]["approved_by"] is None
    assert ts.rows[tid]["approved_at"] is None
    with pytest.raises(ValueError, match="RUNNING may only be entered from APPROVED"):
        await ts.set_state(tid, TaskState.RUNNING)


# ── routed_to: resolved, recorded, delivery deferred ──────────────────────
async def test_routed_to_is_recorded_and_delivery_is_explicitly_deferred():
    """routed_to says WHO the question is for; it is not a delivery receipt.

    Doc 07 §3.6: this doc "defines no channel of its own". The question reaches its human
    via the draft card, which renders from the pending clarify_items rows — so nothing is
    lost by not sending, and sending would be the new messaging path §3.8 forbids.
    """
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts, owner="Sam")
    res = await run_clarify(
        [_item(tid, owner="Sam", attributed_person="Sam", has_scope=True)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    o = res.outcomes[0]
    assert o.routed_to == "Sam"
    assert o.delivery_deferred is True, "delivery must be explicitly deferred, not silent"
    assert o.written is True

    # The question is still reachable: it is a pending clarify row, which is what the
    # draft card renders from.
    pending = await cs.pending_for_meeting(MEETING)
    assert [p["question"] for p in pending] == [o.question]


async def test_an_unroutable_question_is_pending_not_deferred():
    """The two states are distinct: nobody to ask vs. resolved-but-not-sent."""
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    tid = await _seed(ts)
    res = await run_clarify(
        [_item(tid, owner=UNRESOLVED, attributed_person=None)],
        tenant_id=TENANT, meeting_id=MEETING,
        clarify_store=cs, task_store=ts, channels=CHANNELS,
    )
    o = res.outcomes[0]
    assert o.routed_to is None
    assert o.pending is True
    assert o.delivery_deferred is False
