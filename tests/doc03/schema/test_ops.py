"""AC-SCHEMA-12,13,14,26 — the delta operations and NoteDelta caps.

NoteDelta.ops<=40, NoteDelta.current_goal<=200, PatchOp.supersede_reason<=300
(with supersede-not-erase applier semantics), CloseOp contract.
"""
import pytest
from pydantic import ValidationError

from scribe.parse import Entry, NoteStore
from scribe.schema import AddOp, Claim, CloseOp, NoteDelta, PatchOp


def _add_op():
    return AddOp(
        entry=Claim(text="c", speaker="A", said_at_s=1.0, firmness="firm", provenance="observed")
    )


# AC-SCHEMA-12 — NoteDelta.ops rejects >40 operations
def test_ops_rejects_forty_one():
    with pytest.raises(ValidationError):
        NoteDelta(ops=[_add_op() for _ in range(41)])


def test_ops_accepts_forty():
    assert len(NoteDelta(ops=[_add_op() for _ in range(40)]).ops) == 40


# AC-SCHEMA-13 — NoteDelta.current_goal rejects >200
def test_current_goal_rejects_201():
    with pytest.raises(ValidationError):
        NoteDelta(ops=[], current_goal="x" * 201)


def test_current_goal_accepts_200_and_none():
    assert len(NoteDelta(ops=[], current_goal="x" * 200).current_goal) == 200
    assert NoteDelta(ops=[], current_goal=None).current_goal is None
    assert NoteDelta(ops=[]).current_goal is None


# AC-SCHEMA-14 — PatchOp.supersede_reason<=300; old value retained superseded
def test_supersede_reason_rejects_301():
    with pytest.raises(ValidationError):
        PatchOp(target_id="e1", changes={"status": "final"}, supersede_reason="x" * 301)


def test_supersede_reason_accepts_300():
    op = PatchOp(target_id="e1", changes={"status": "final"}, supersede_reason="x" * 300)
    assert len(op.supersede_reason) == 300


def test_patch_supersedes_not_erases_old_value():
    store = NoteStore({"e1": Entry(id="e1", current={"status": "forming"})})
    op = PatchOp(target_id="e1", changes={"status": "final"}, supersede_reason="finalized")
    store.apply(op)
    entry = store.get("e1")
    assert entry.current["status"] == "final"          # new value is set
    assert {"status": "forming"} in entry.history        # old value retained superseded


# AC-SCHEMA-26 — CloseOp contract: op fixed, target_id required, resolution<=300
def test_close_op_fixed():
    with pytest.raises(ValidationError):
        CloseOp(op="x", target_id="e1", resolution="ok")


def test_close_resolution_rejects_301():
    with pytest.raises(ValidationError):
        CloseOp(target_id="e1", resolution="x" * 301)


def test_close_op_valid():
    op = CloseOp(op="close", target_id="e1", resolution="ok")
    assert op.op == "close" and op.target_id == "e1" and op.resolution == "ok"


def test_close_target_id_required():
    with pytest.raises(ValidationError):
        CloseOp(resolution="ok")
