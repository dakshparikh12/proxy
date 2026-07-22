"""AC-CORR-11 and AC-CORR-12 — the null-dependency gate LOGIC (§3.6).

These two criteria carry ``dependency_class: null``: they are pure in-memory
classification/scope facts about the notes-injection gate, so they run with no
Postgres. This file also unit-checks the classifier (``is_high_stakes``) across
the ordinary vs high-stakes split and the receipt shape — the in-memory core the
db tests then prove end-to-end on real Postgres.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

import pytest

from scribe.corrections import (
    Correction,
    WorldTouchingAction,
    acknowledgement_line,
    apply_correction,
    is_high_stakes,
    is_notes_correction,
    notes_gate_governs,
)
from scribe.schema import CloseOp, DecisionStatus, PatchOp, Reversibility

_T = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _corr(op: Any, prior: Any = None, corrector: str = "Sam") -> Correction:
    return Correction(
        target_id=op.target_id,
        op=op,
        corrector=corrector,
        corrected_at=_T,
        prior_entry=prior,
    )


# ── classification: the ordinary (silent) split ───────────────────────────────
def test_firmness_bump_is_not_high_stakes() -> None:
    c = _corr(PatchOp(target_id="E1", changes={"firmness": "firm"}, supersede_reason="firmer"))
    assert is_high_stakes(c) is False


def test_action_item_tweak_is_not_high_stakes() -> None:
    c = _corr(PatchOp(target_id="A1", changes={"owner": "Ada"}, supersede_reason="reassign"))
    assert is_high_stakes(c) is False


def test_forming_lean_is_not_high_stakes() -> None:
    c = _corr(
        PatchOp(target_id="D1", changes={"leans": {"Sam": "for"}}, supersede_reason="lean"),
        prior={"status": "forming"},
    )
    assert is_high_stakes(c) is False


def test_open_question_edit_is_not_high_stakes() -> None:
    c = _corr(PatchOp(target_id="Q1", changes={"text": "reworded?"}, supersede_reason="clarify"))
    assert is_high_stakes(c) is False


def test_close_of_open_question_is_not_high_stakes() -> None:
    # Closing an ordinary (not already final/irreversible) entry is silent.
    c = _corr(CloseOp(target_id="Q1", resolution="answered: yes"), prior={"resolved": False})
    assert is_high_stakes(c) is False


# ── classification: the high-stakes (spoken) split ────────────────────────────
def test_sets_decision_final_is_high_stakes_enum() -> None:
    c = _corr(PatchOp(target_id="D1", changes={"status": DecisionStatus.final}, supersede_reason="x"))
    assert is_high_stakes(c) is True


def test_sets_decision_final_is_high_stakes_value() -> None:
    c = _corr(PatchOp(target_id="D1", changes={"status": "final"}, supersede_reason="x"))
    assert is_high_stakes(c) is True


def test_sets_irreversible_is_high_stakes_enum() -> None:
    c = _corr(
        PatchOp(target_id="D1", changes={"reversibility": Reversibility.irreversible}, supersede_reason="x")
    )
    assert is_high_stakes(c) is True


def test_sets_irreversible_is_high_stakes_value() -> None:
    c = _corr(
        PatchOp(target_id="D1", changes={"reversibility": "irreversible"}, supersede_reason="x")
    )
    assert is_high_stakes(c) is True


def test_close_of_already_final_entry_is_high_stakes() -> None:
    c = _corr(CloseOp(target_id="D1", resolution="finalized"), prior={"status": "final"})
    assert is_high_stakes(c) is True


def test_close_of_already_irreversible_entry_is_high_stakes() -> None:
    c = _corr(CloseOp(target_id="D1", resolution="locked"), prior={"reversibility": "irreversible"})
    assert is_high_stakes(c) is True


def test_patch_touching_already_final_entry_is_high_stakes() -> None:
    # AC-CORR-10 core: re-affirming an already-final entry still fires the gate.
    c = _corr(
        PatchOp(target_id="D1", changes={"text": "reworded"}, supersede_reason="tidy"),
        prior={"status": "final"},
    )
    assert is_high_stakes(c) is True


# ── AC-CORR-05-NEG (logic half): tweak+final in one patch is NOT ordinary ─────
def test_tweak_combined_with_final_is_high_stakes() -> None:
    c = _corr(
        PatchOp(
            target_id="A1",
            changes={"text": "Ship it", "status": DecisionStatus.final},
            supersede_reason="tweak+final",
        )
    )
    assert is_high_stakes(c) is True


# ── the receipt line (audible receipt shape, not a modal) ─────────────────────
def test_acknowledgement_line_is_one_line_and_not_a_question() -> None:
    c = _corr(PatchOp(target_id="D1", changes={"status": DecisionStatus.final, "text": "Friday"}, supersede_reason="x"))
    line = acknowledgement_line(c)
    assert isinstance(line, str) and line
    assert "\n" not in line  # ONE line
    assert "?" not in line  # a receipt, never an "are you sure?" prompt


def test_acknowledgement_line_for_close_uses_resolution() -> None:
    c = _corr(CloseOp(target_id="D1", resolution="finalized Friday"), prior={"status": "final"})
    assert "finalized Friday" in acknowledgement_line(c)


# ── AC-CORR-11: the spoken confirm is a RECEIPT, not a blocking prompt ─────────
def test_speak_seam_is_fire_once_not_a_blocking_await() -> None:
    """The receipt is emitted via a fire-once ``await speak(line)`` — a one-way
    emit, never an await on a human approval. ``apply_correction`` never returns
    an 'approval-pending' state and never consults ``speak``'s return value; the
    signature has no approval/await-response parameter (F-CORR-BLOCKING-CONFIRM).
    """
    sig = inspect.signature(apply_correction)
    params = set(sig.parameters)
    # No approval-gate parameter exists on the apply path.
    for forbidden in ("approve", "approval", "wait_for_ack", "block", "confirm_cb"):
        assert forbidden not in params
    # ``speak`` is the ONLY speech seam and it is optional (silent path passes None).
    assert "speak" in params
    assert sig.parameters["speak"].default is None


def test_apply_result_carries_no_pending_state() -> None:
    """The ApplyResult is a success receipt with no 'approval_pending'/'blocked'
    field — the pipeline has no wait-state to enter (AC-CORR-11)."""
    from scribe.corrections import ApplyResult

    fields = set(ApplyResult.__dataclass_fields__)
    assert not (fields & {"approval_pending", "blocked", "waiting", "pending"})
    assert {"committed", "gate_fired", "spoken"} <= fields


# ── AC-CORR-12: the notes gate governs NOTES corrections ONLY ─────────────────
def test_notes_gate_governs_a_correction() -> None:
    c = _corr(PatchOp(target_id="E1", changes={"firmness": "firm"}, supersede_reason="x"))
    assert is_notes_correction(c) is True
    assert notes_gate_governs(c) is True


def test_notes_gate_does_not_govern_a_world_touching_action() -> None:
    # A calendar invite / staged PR — Law-3 human-click path, NOT this gate.
    action = WorldTouchingAction(kind="calendar_invite", detail="team sync")
    assert is_notes_correction(action) is False
    assert notes_gate_governs(action) is False


@pytest.mark.parametrize("obj", [object(), "just a string", 42, None, {"kind": "x"}])
def test_notes_gate_does_not_govern_arbitrary_objects(obj: object) -> None:
    assert notes_gate_governs(obj) is False


def test_world_touching_action_draws_no_notes_gate_speak() -> None:
    """A WorldTouchingAction is never a Correction, so it never enters the apply
    path and never draws a notes-gate spoken receipt (F-CORR-GATE-SCOPE-EXCEEDED).
    """
    action = WorldTouchingAction(kind="staged_pr")
    # The gate's scope predicate is the routing boundary: not governed -> the
    # correction apply path (the only speaker) is never taken for this object.
    assert notes_gate_governs(action) is False
