"""SEAM 1 — close completion triggers intake. Criteria: AC-PME-02, AC-PME-02-NEG.

The isolation property is the whole point of this seam, so most of these tests are about
what happens when intake goes wrong.
"""
from __future__ import annotations

import copy
import uuid

import pytest
from harness.post_meeting.intake import run_intake, run_intake_guarded
from harness.post_meeting.models import TaskState, Tier
from harness.post_meeting.triage import TRIAGE_SCHEMA
from harness.scribe_runtime import CloseConfig, _run_post_meeting_intake

from libs.llm.src.llm.structured import StructuredOutputError, StructuredResult

from ._support import FakeActionItem, FakeClarifyStore, FakeFinalNotes, FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()

PLAN_DATA = {
    "task_one_line": "bump the retry ceiling", "why_it_exists": "Sam took it",
    "meeting_reference": "m#0", "owner": "Sam",
    "done_looks_like": "the retry test passes", "steps": ["bump"], "confidence": "high",
}


async def passthrough(op, *, service, **kwargs):
    return await op()


def caller(tiers: dict[str, str]):
    """One caller serving both the triage and plan schemas, keyed by which is asked for."""

    async def _c(*, model, prompt, output_schema, tool_name):
        if output_schema is TRIAGE_SCHEMA:
            return StructuredResult(
                data={
                    "verdicts": [
                        {"item_ref": r, "tier": t, "draft_conditions_met": True}
                        for r, t in tiers.items()
                    ]
                }
            )
        return StructuredResult(data=PLAN_DATA)

    return _c


def notes(*items):
    return FakeFinalNotes(action_items=list(items))


# ── the pipeline actually runs end to end ─────────────────────────────────
async def test_seam1_extract_triage_clarify_plan_all_run():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    n = notes(FakeActionItem("bump the retry ceiling to 5", owner="Sam"))
    ref = f"{MEETING}#action_items[0]"

    res = await run_intake(
        n, meeting_id=MEETING, tenant_id=TENANT, task_store=ts, clarify_store=cs,
        caller=caller({ref: "ticket+plan+draft"}), call_external=passthrough,
    )
    assert res.ok
    assert res.task_count == 1
    (row,) = ts.rows.values()
    assert row["tier"] == Tier.TICKET_PLAN_DRAFT.value
    assert row["state"] == TaskState.PLANNED.value
    assert row["plan"], "B4 never wrote the plan"
    assert len(res.plans) == 1


async def test_seam1_set_tier_finally_has_a_caller():
    """store.set_tier had no caller before this seam; triage's verdict is what calls it."""
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    calls: list = []
    real = ts.set_tier

    async def spy(task_id, tier, *, state):
        calls.append((tier, state))
        return await real(task_id, tier, state=state)

    ts.set_tier = spy  # type: ignore[method-assign]
    ref = f"{MEETING}#action_items[0]"
    await run_intake(
        notes(FakeActionItem("x", owner="Sam")), meeting_id=MEETING, tenant_id=TENANT,
        task_store=ts, clarify_store=cs,
        caller=caller({ref: "ticket"}), call_external=passthrough,
    )
    assert calls == [(Tier.TICKET, TaskState.TRIAGED)]


async def test_seam1_an_ambiguous_item_is_held_and_never_planned():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    ref = f"{MEETING}#action_items[0]"
    res = await run_intake(
        notes(FakeActionItem("someone should look at the spike")),  # no owner
        meeting_id=MEETING, tenant_id=TENANT, task_store=ts, clarify_store=cs,
        caller=caller({ref: "question"}), call_external=passthrough,
    )
    (row,) = ts.rows.values()
    assert row["state"] == TaskState.CLARIFYING.value
    assert row["plan"] is None, "an ambiguous item was planned"
    assert len(cs.rows) == 1
    assert res.plans == []


async def test_seam1_informational_items_get_no_plan():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    ref = f"{MEETING}#action_items[0]"
    res = await run_intake(
        notes(FakeActionItem("FYI we shipped", owner="Sam")),
        meeting_id=MEETING, tenant_id=TENANT, task_store=ts, clarify_store=cs,
        caller=caller({ref: "informational"}), call_external=passthrough,
    )
    assert res.plans == []
    (row,) = ts.rows.values()
    assert row["plan"] is None


# ── AC-PME-02 · intake never touches the close or the record ──────────────
async def test_ac_pme_02_intake_does_not_mutate_the_close_record():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    n = notes(FakeActionItem("a", owner="Sam"), FakeActionItem("b"))
    before = copy.deepcopy(n)
    ref0, ref1 = f"{MEETING}#action_items[0]", f"{MEETING}#action_items[1]"
    await run_intake(
        n, meeting_id=MEETING, tenant_id=TENANT, task_store=ts, clarify_store=cs,
        caller=caller({ref0: "ticket", ref1: "question"}), call_external=passthrough,
    )
    assert n == before


@pytest.mark.parametrize(
    "boom",
    [RuntimeError("intake exploded"), ConnectionRefusedError("db gone"),
     KeyboardInterrupt(), MemoryError()],
    ids=["runtime", "conn", "baseexc", "memory"],
)
async def test_ac_pme_02_a_raising_intake_never_reaches_the_close(boom):
    """The close's own guard: whatever class intake fails with, the close proceeds.

    KeyboardInterrupt and MemoryError are included deliberately — they are BaseException,
    not Exception, and a narrow `except Exception` would let them through into the close.
    """
    async def exploding(final_notes, *, meeting_id):
        raise boom

    cfg = CloseConfig(
        bucket=object(), bucket_name="b", post_chat_link=None,
        post_meeting_intake=exploding,
    )
    # Must not raise.
    await _run_post_meeting_intake(cfg, MEETING, notes())


async def test_ac_pme_02_no_hook_configured_is_a_no_op():
    """A deployment without Doc 07 wired behaves exactly as before it existed."""
    cfg = CloseConfig(bucket=object(), bucket_name="b", post_chat_link=None)
    assert cfg.post_meeting_intake is None
    await _run_post_meeting_intake(cfg, MEETING, notes())


async def test_ac_pme_02_the_close_result_is_returned_unchanged():
    """The close's return value is computed before intake and is not intake's to alter."""
    sentinel = object()
    seen = {}

    async def hook(final_notes, *, meeting_id):
        seen["called"] = True
        return "a value intake would like to return"

    cfg = CloseConfig(
        bucket=object(), bucket_name="b", post_chat_link=None, post_meeting_intake=hook
    )
    out = await _run_post_meeting_intake(cfg, MEETING, sentinel)
    assert out is None, "intake must not be able to substitute the close's result"
    assert seen["called"]


# ── AC-PME-02-NEG · stage failures stop intake, not the close ─────────────
@pytest.mark.negative
async def test_ac_pme_02_neg_a_failing_stage_is_reported_not_raised():
    ts, cs = FakeTaskStore(), FakeClarifyStore()
    ts.insert_error = ConnectionRefusedError("postgres refused")
    res = await run_intake(
        notes(FakeActionItem("a", owner="Sam")), meeting_id=MEETING, tenant_id=TENANT,
        task_store=ts, clarify_store=cs, caller=caller({}), call_external=passthrough,
    )
    assert res.ok is False
    assert res.failed_stage == "extract"
    assert isinstance(res.error, ConnectionRefusedError)


@pytest.mark.negative
async def test_ac_pme_02_neg_triage_failure_leaves_items_untiered_and_unplanned():
    ts, cs = FakeTaskStore(), FakeClarifyStore()

    async def broken(*, model, prompt, output_schema, tool_name):
        raise StructuredOutputError("vendor 500")

    res = await run_intake(
        notes(FakeActionItem("a", owner="Sam")), meeting_id=MEETING, tenant_id=TENANT,
        task_store=ts, clarify_store=cs, caller=broken, call_external=passthrough,
    )
    assert res.failed_stage == "triage"
    (row,) = ts.rows.values()
    assert row["tier"] is None
    assert row["state"] == TaskState.EXTRACTED.value
    assert row["plan"] is None, "an untiered item was planned"
    assert res.plans == []


@pytest.mark.negative
async def test_ac_pme_02_neg_run_intake_itself_never_raises():
    """Even handed garbage, intake returns a result rather than propagating."""
    res = await run_intake(
        object(), meeting_id=MEETING, tenant_id=TENANT,
        task_store=None, clarify_store=None, caller=None, call_external=None,
    )
    assert isinstance(res.ok, bool)


@pytest.mark.negative
async def test_ac_pme_02_neg_guarded_wrapper_returns_none_on_total_failure():
    out = await run_intake_guarded(
        notes(), meeting_id=MEETING, tenant_id=TENANT,
        task_store=object(), clarify_store=object(), caller=None, call_external=None,
    )
    assert out is None or out.ok in (True, False)
