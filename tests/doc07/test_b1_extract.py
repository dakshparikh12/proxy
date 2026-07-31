"""B1 — extract. Criteria: AC-PME-02, AC-PME-02-NEG, AC-PME-05, AC-PME-05-NEG."""
from __future__ import annotations

import copy
import uuid

import pytest
from control_plane.post_meeting.extract import extract_items, resolve_owner, run_extract
from control_plane.post_meeting.models import UNRESOLVED, Source, TaskState

from ._support import FakeActionItem, FakeFinalNotes, FakeTaskStore

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()


# ── AC-PME-05 · owner is UNRESOLVED or from the room, never inferred ──────
async def test_ac_pme_05_owner_from_room_is_kept():
    notes = FakeFinalNotes(action_items=[FakeActionItem("bump retry ceiling", owner="Sam")])
    store = FakeTaskStore()
    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
    assert res.ok
    assert res.tasks[0].owner == "Sam"


async def test_ac_pme_05_no_owner_becomes_unresolved_not_empty():
    """UNRESOLVED is a REAL value, distinct from empty string and from None."""
    notes = FakeFinalNotes(
        action_items=[
            FakeActionItem("look at the error spike"),
            FakeActionItem("tidy the logs", owner=""),
            FakeActionItem("check the alert", owner="   "),
            FakeActionItem("rotate the key", owner=None),
        ]
    )
    store = FakeTaskStore()
    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
    assert res.ok
    assert [t.owner for t in res.tasks] == [UNRESOLVED] * 4
    for t in res.tasks:
        assert t.owner != ""
        assert t.owner is not None
    assert res.unresolved_count == 4


async def test_ac_pme_05_owner_never_inferred_from_decoys():
    """The three decoys the criterion names: seniority, speaking volume, file authorship.

    ``resolve_owner`` takes ONE argument — the owner the room stated. There is no
    parameter through which a decoy could enter, which is the structural reason this
    holds rather than a behavioural one.
    """
    senior_speaker = "Priya (VP Eng)"
    dominant_speaker = "Marcus"
    last_file_author = "dakshparikh12"

    notes = FakeFinalNotes(action_items=[FakeActionItem("investigate the spike")])
    store = FakeTaskStore()
    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)

    owner = res.tasks[0].owner
    assert owner == UNRESOLVED
    assert owner not in {senior_speaker, dominant_speaker, last_file_author}

    import inspect

    sig = inspect.signature(resolve_owner)
    assert list(sig.parameters) == ["raw_owner"], (
        "resolve_owner must not accept roster, transcript, or authorship inputs — "
        "inference must be impossible by signature, not merely discouraged"
    )


async def test_ac_pme_05_literal_unresolved_in_notes_is_not_an_owner():
    """A close object that literally says UNRESOLVED has not named an owner.

    Asserted on ``extract_items``, which carries ``owner_from_room`` — the flag that
    distinguishes "the room said Sam" from "we fell back". ``TaskRecord`` deliberately
    does not carry it: once persisted, ``owner == UNRESOLVED`` IS the distinction.
    """
    notes = FakeFinalNotes(action_items=[FakeActionItem("x", owner="UNRESOLVED")])
    items, _ = extract_items(notes, meeting_id=MEETING)
    assert items[0].owner == UNRESOLVED
    assert items[0].owner_from_room is False

    named, _ = extract_items(
        FakeFinalNotes(action_items=[FakeActionItem("x", owner="Sam")]), meeting_id=MEETING
    )
    assert named[0].owner_from_room is True


# ── AC-PME-05-NEG · degraded read still yields UNRESOLVED, never a guess ──
@pytest.mark.negative
async def test_ac_pme_05_neg_degraded_read_never_widens_concrete_owners():
    clean = FakeFinalNotes(
        action_items=[
            FakeActionItem("a", owner="Sam"),
            FakeActionItem("b", owner="Priya"),
            FakeActionItem("c"),
        ]
    )
    # Truncated/malformed: two items unreadable, one owner lost.
    degraded = FakeFinalNotes(
        action_items=[FakeActionItem("a", owner="Sam"), FakeActionItem(""), object()]
    )

    s1, s2 = FakeTaskStore(), FakeTaskStore()
    r_clean = await run_extract(clean, meeting_id=MEETING, tenant_id=TENANT, store=s1)
    r_deg = await run_extract(degraded, meeting_id=MEETING, tenant_id=TENANT, store=s2)

    concrete_clean = sum(1 for t in r_clean.tasks if t.owner != UNRESOLVED)
    concrete_deg = sum(1 for t in r_deg.tasks if t.owner != UNRESOLVED)
    assert concrete_deg <= concrete_clean, "degraded input widened the concrete-owner set"
    assert r_deg.read_degraded is True, "degradation must be recorded, not hidden"
    for t in r_deg.tasks:
        assert t.read_degraded is True


@pytest.mark.negative
async def test_ac_pme_05_neg_missing_action_items_is_degraded_not_empty():
    class NoItems:
        summary = "s"

    store = FakeTaskStore()
    res = await run_extract(NoItems(), meeting_id=MEETING, tenant_id=TENANT, store=store)
    assert res.ok
    assert res.tasks == []
    assert res.read_degraded is True, (
        "an unreadable action_items is a degraded read, not a meeting with no actions"
    )


# ── AC-PME-02 · total failure leaves the close and the record untouched ───
async def test_ac_pme_02_close_record_is_never_mutated():
    notes = FakeFinalNotes(
        action_items=[FakeActionItem("a", owner="Sam"), FakeActionItem("b")]
    )
    before = copy.deepcopy(notes)
    store = FakeTaskStore()
    await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
    assert notes == before, "extraction mutated the close record"


async def test_ac_pme_02_extract_never_raises_on_store_failure():
    notes = FakeFinalNotes(action_items=[FakeActionItem("a", owner="Sam")])
    store = FakeTaskStore()
    store.insert_error = RuntimeError("postgres refused the connection")

    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
    assert res.ok is False
    assert isinstance(res.error, RuntimeError)
    assert res.tasks == []


async def test_ac_pme_02_ordered_close_completes_when_extract_fails():
    """The close sequence runs to completion with a failing post-meeting component.

    Unit-tier proxy for the ordered close: the steps are recorded in order and extraction
    is invoked where Doc 07 §2 places it — strictly AFTER the record is written. The real
    close path is exercised at the e2e rung, which needs Postgres and GCS.
    """
    steps: list[str] = []
    notes = FakeFinalNotes(action_items=[FakeActionItem("a", owner="Sam")])
    store = FakeTaskStore()
    store.insert_error = RuntimeError("db down")

    async def ordered_close() -> None:
        steps.append("freeze-notes")
        steps.append("close-pass")
        steps.append("render+gcs-write")
        steps.append("chat-link")
        # Doc 07 begins here — after the record is written.
        await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
        steps.append("teardown")

    await ordered_close()
    assert steps == [
        "freeze-notes",
        "close-pass",
        "render+gcs-write",
        "chat-link",
        "teardown",
    ]


# ── AC-PME-02-NEG · a database fault still leaves the close intact ────────
@pytest.mark.negative
async def test_ac_pme_02_neg_db_fault_leaves_no_partial_rows_and_is_reported(caplog):
    notes = FakeFinalNotes(
        action_items=[FakeActionItem("a", owner="Sam"), FakeActionItem("b")]
    )
    before = copy.deepcopy(notes)
    store = FakeTaskStore()
    store.insert_error = ConnectionRefusedError("postgres refuses connections")

    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)

    assert res.ok is False
    assert isinstance(res.error, ConnectionRefusedError)
    assert store.rows == {}, "a failed insert must leave no partial task row"
    assert notes == before
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "the database fault must be reported, not swallowed"
    )


@pytest.mark.negative
async def test_ac_pme_02_neg_partial_progress_is_kept_and_reported():
    """Third insert fails: the two that succeeded are returned WITH the error."""
    notes = FakeFinalNotes(
        action_items=[FakeActionItem(t) for t in ("a", "b", "c", "d")]
    )
    store = FakeTaskStore()
    calls = {"n": 0}
    real_insert = store.insert_task

    async def flaky(task):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionResetError("connection dropped mid-transaction")
        return await real_insert(task)

    store.insert_task = flaky  # type: ignore[method-assign]
    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)

    assert res.ok is False
    assert len(res.tasks) == 2, "partial progress must be reported, not silently discarded"
    assert isinstance(res.error, ConnectionResetError)


# ── extraction shape ──────────────────────────────────────────────────────
async def test_extracted_items_land_at_extracted_with_no_tier():
    notes = FakeFinalNotes(action_items=[FakeActionItem("a", owner="Sam")])
    store = FakeTaskStore()
    res = await run_extract(notes, meeting_id=MEETING, tenant_id=TENANT, store=store)
    (task,) = res.tasks
    assert task.state is TaskState.EXTRACTED
    assert task.tier is None, "B1 extracts; B2 tiers"
    assert task.source is Source.CLOSE_ITEM
    assert task.item_ref.endswith("#action_items[0]")


async def test_item_ref_points_back_at_the_meeting_line():
    notes = FakeFinalNotes(action_items=[FakeActionItem("a"), FakeActionItem("b")])
    items, _ = extract_items(notes, meeting_id=MEETING)
    assert [i.item_ref for i in items] == [
        f"{MEETING}#action_items[0]",
        f"{MEETING}#action_items[1]",
    ]
