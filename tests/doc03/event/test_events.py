"""Doc 03 event-emitter unit tests (AC-EVENT-01..10).

The emitter fires the seven CLOSED material-change kinds to a mock Doc 04 sink on
delta application; chitchat / running-context / context-line emit NOTHING; the
layer speaks nothing (pipe, never the mouth); the decision/action chat-record line
is a deterministic formatter keyed on the committed delta, deduped by
meeting_revision, honoring the disable toggle. All unit — a fake sink IS the
correct seam boundary (03-MEETING-UNDERSTANDING §3.5).
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from contracts import MaterialChangeKind
from scribe.events import (
    ChatRecorder,
    CollectingSink,
    MaterialChangeEvent,
    apply_delta,
    emit_events_for_delta,
    focused_context_slice,
    format_chat_line,
    record_chat_lines,
)
from scribe.schema import (
    ActionItem,
    AddOp,
    Claim,
    CloseOp,
    ContextLine,
    Decision,
    DecisionStatus,
    Firmness,
    OpenQuestion,
    Provenance,
    Reversibility,
    NoteDelta,
)


# --------------------------------------------------------------------------- #
# Synthetic-entry builders (deterministic; no model, no I/O).                 #
# --------------------------------------------------------------------------- #
def a_claim(text: str = "checkout ships Friday", contradicts: str | None = None) -> Claim:
    return Claim(
        text=text,
        speaker="sam",
        said_at_s=12.0,
        firmness=Firmness.firm,
        provenance=Provenance.observed,
        contradicts=contradicts,
    )


def a_forming_decision(text: str = "lean toward shipping Friday") -> Decision:
    return Decision(
        text=text,
        status=DecisionStatus.forming,
        reversibility=Reversibility.easy,
    )


def a_final_decision(text: str = "ship checkout Friday") -> Decision:
    return Decision(
        text=text,
        status=DecisionStatus.final,
        reversibility=Reversibility.hard,
    )


def an_action(text: str = "fix the retry test", owner: str | None = "Sam") -> ActionItem:
    return ActionItem(text=text, owner=owner, due="by Fri")


def an_open_question(text: str = "which region first?") -> OpenQuestion:
    return OpenQuestion(text=text, raised_by="lee", resolved=False)


def a_closed_question(text: str = "which region first?") -> OpenQuestion:
    return OpenQuestion(text=text, raised_by="lee", resolved=True)


def a_context_line(text: str = "some chitchat") -> ContextLine:
    return ContextLine(text=text)


def delta(*ops: object, current_goal: str | None = None) -> NoteDelta:
    return NoteDelta(ops=list(ops), current_goal=current_goal)


# --------------------------------------------------------------------------- #
# T-EVENT-01 — AC-EVENT-01: all seven kinds fire, nothing else.               #
# --------------------------------------------------------------------------- #
def test_event_01_all_seven_material_change_kinds_fire_and_nothing_else() -> None:
    sink = CollectingSink()

    # Seven distinct synthetic deltas, each matching exactly one kind.
    d_claim = delta(AddOp(entry=a_claim()))
    d_forming = delta(AddOp(entry=a_forming_decision()))
    d_final = delta(AddOp(entry=a_final_decision()))
    d_contradiction = delta(AddOp(entry=a_claim(text="ships Monday", contradicts="c1")))
    d_action = delta(AddOp(entry=an_action()))
    d_qopen = delta(AddOp(entry=an_open_question()))
    d_qclosed = delta(AddOp(entry=a_closed_question()))

    for d in (d_claim, d_forming, d_final, d_contradiction, d_action, d_qopen, d_qclosed):
        apply_delta(d, sink=sink, meeting_revision=1)

    kinds = [e.kind for e in sink.events]
    assert kinds == [
        MaterialChangeKind.CLAIM_LANDED_CHECKABLE,
        MaterialChangeKind.DECISION_FORMING,
        MaterialChangeKind.DECISION_FINAL,
        MaterialChangeKind.CONTRADICTION,
        MaterialChangeKind.ACTION_ITEM,
        MaterialChangeKind.QUESTION_OPEN,
        MaterialChangeKind.QUESTION_CLOSED,
    ]
    # Exactly one of each of the seven; no other type present.
    for k in MaterialChangeKind:
        assert kinds.count(k) == 1
    assert len(sink.events) == 7


def test_event_01_question_closed_also_fires_via_close_op() -> None:
    # The natural close path: a CloseOp resolving an existing OpenQuestion.
    sink = CollectingSink()
    existing = an_open_question()
    apply_delta(
        delta(CloseOp(target_id="q1", resolution="answered: us-east")),
        sink=sink,
        meeting_revision=1,
        resolve_target=lambda tid: existing if tid == "q1" else None,
    )
    assert [e.kind for e in sink.events] == [MaterialChangeKind.QUESTION_CLOSED]


# --------------------------------------------------------------------------- #
# T-EVENT-02 — AC-EVENT-02: entry + focused (non-full) context slice.         #
# --------------------------------------------------------------------------- #
def test_event_02_event_carries_full_entry_and_focused_context_slice() -> None:
    sink = CollectingSink()
    triggering = a_claim(text="checkout ships Friday")
    # A larger surrounding notes state — the slice must NOT be the whole thing.
    surrounding = [a_context_line(f"note {i}") for i in range(20)]

    apply_delta(
        delta(AddOp(entry=triggering)),
        sink=sink,
        meeting_revision=1,
        surrounding=surrounding,
    )

    assert len(sink.events) == 1
    ev = sink.events[0]
    # Full triggering entry (all fields match the committed delta).
    assert ev.entry is triggering
    assert isinstance(ev.entry, Claim)
    assert ev.entry.text == "checkout ships Friday"
    assert ev.entry.speaker == "sam"
    assert ev.entry.firmness is Firmness.firm
    # Focused slice: non-empty, and strictly narrower than the full context.
    assert len(ev.context_slice) > 0
    assert len(ev.context_slice) < len(surrounding)
    # It contains the triggering entry and is scoped to relevant surrounding notes.
    assert triggering in ev.context_slice
    assert ev.is_complete()


def test_event_02_incomplete_payload_is_not_emitted_as_complete() -> None:
    # A payload with an empty context slice is not "complete".
    incomplete = MaterialChangeEvent(
        kind=MaterialChangeKind.CLAIM_LANDED_CHECKABLE,
        entry=a_claim(),
        context_slice=(),
        meeting_revision=1,
    )
    assert incomplete.is_complete() is False
    # The emit path always produces a non-empty slice (triggering entry included),
    # even from an empty surrounding state.
    sink = CollectingSink()
    emit_events_for_delta(
        delta(AddOp(entry=a_claim())), sink=sink, meeting_revision=1, surrounding=[]
    )
    assert len(sink.events) == 1
    assert sink.events[0].is_complete()
    assert len(sink.events[0].context_slice) > 0


def test_event_02_context_slice_never_full_meeting_context() -> None:
    full = [a_context_line(f"line {i}") for i in range(50)]
    trig = a_final_decision()
    sl = focused_context_slice(trig, full)
    assert 0 < len(sl) < len(full)
    assert trig in sl


# --------------------------------------------------------------------------- #
# T-EVENT-03 — AC-EVENT-03: chitchat emits zero events.                       #
# --------------------------------------------------------------------------- #
def test_event_03_chitchat_delta_emits_zero_events() -> None:
    sink = CollectingSink()
    before = sink.count
    apply_delta(
        delta(AddOp(entry=a_context_line("just banter"))),
        sink=sink,
        meeting_revision=1,
    )
    assert sink.count == 0
    assert sink.count == before  # unchanged after the chitchat delta


# --------------------------------------------------------------------------- #
# T-EVENT-04 — AC-EVENT-04: running-context update emits zero events.         #
# --------------------------------------------------------------------------- #
def test_event_04_running_context_update_emits_zero_events() -> None:
    sink = CollectingSink()
    # A running-context update: only current_goal set, no material op.
    apply_delta(
        delta(current_goal="unblock the checkout flow"),
        sink=sink,
        meeting_revision=1,
    )
    assert sink.count == 0


# --------------------------------------------------------------------------- #
# T-EVENT-05 — AC-EVENT-05: no speech acts, no room-delivery from this layer. #
# --------------------------------------------------------------------------- #
def test_event_05_emitter_produces_no_speech_and_no_room_delivery() -> None:
    speak = Mock()
    send_chat = Mock()
    show_screen = Mock()

    sink = CollectingSink()
    deltas = [
        delta(AddOp(entry=a_claim())),
        delta(AddOp(entry=a_final_decision())),
        delta(AddOp(entry=an_action())),
        delta(AddOp(entry=a_context_line("aside"))),
        delta(current_goal="ship it"),
        delta(AddOp(entry=an_open_question())),
    ]
    for d in deltas:
        apply_delta(d, sink=sink, meeting_revision=1)

    # The emitter never reaches for a room-delivery surface.
    assert speak.call_count == 0
    assert send_chat.call_count == 0
    assert show_screen.call_count == 0
    # Material events still flowed to Doc 04 (the pipe worked) without any mouth.
    assert sink.count == 4  # claim, decision-final, action, question-open


def test_event_05_events_module_exposes_no_room_delivery_symbols() -> None:
    import scribe.events as mod

    for forbidden in ("speak", "send_chat", "show_screen"):
        assert not hasattr(mod, forbidden), f"emitter must not own {forbidden}"


# --------------------------------------------------------------------------- #
# T-EVENT-06 — AC-EVENT-06: chat line is deterministic formatter, no wake/LLM.#
# --------------------------------------------------------------------------- #
def _final_decision_event(rev: int = 7) -> MaterialChangeEvent:
    return MaterialChangeEvent(
        kind=MaterialChangeKind.DECISION_FINAL,
        entry=a_final_decision("ship checkout Friday"),
        context_slice=(a_final_decision("ship checkout Friday"),),
        meeting_revision=rev,
    )


def test_event_06_chat_line_is_deterministic_never_wake_never_model() -> None:
    wake = Mock()
    llm = Mock()
    poster = Mock()

    ev = _final_decision_event()
    recorder = ChatRecorder(post=poster, enabled=True)

    # Byte-identical output on the same committed delta, twice (determinism).
    out1 = format_chat_line(ev)
    out2 = format_chat_line(ev)
    assert out1 is not None
    assert out1 == out2
    assert out1.encode("utf-8") == out2.encode("utf-8")
    assert out1 == "✓ Decision: ship checkout Friday"

    line = recorder.record(ev)
    assert line == out1
    poster.assert_called_once_with(out1)

    # No wake-turn path, no LLM/model call on the chat-line path.
    assert wake.call_count == 0
    assert llm.call_count == 0


def test_event_06_formatter_output_carries_no_internal_component_name() -> None:
    # Naming law: the user-visible record line never leaks an internal name.
    line = format_chat_line(_final_decision_event())
    assert line is not None
    lowered = line.lower()
    for internal in ("orchestrator", "scribe", "workroom"):
        assert internal not in lowered


# --------------------------------------------------------------------------- #
# T-EVENT-07 — AC-EVENT-07: action-item chat line via deterministic formatter.#
# --------------------------------------------------------------------------- #
def _action_event(rev: int = 3) -> MaterialChangeEvent:
    entry = an_action(text="fix the retry test", owner="Sam")
    return MaterialChangeEvent(
        kind=MaterialChangeKind.ACTION_ITEM,
        entry=entry,
        context_slice=(entry,),
        meeting_revision=rev,
    )


def test_event_07_action_item_chat_line_matches_template_no_wake_no_model() -> None:
    wake = Mock()
    llm = Mock()
    poster = Mock()

    ev = _action_event()
    recorder = ChatRecorder(post=poster, enabled=True)
    line = recorder.record(ev)

    assert line == "☐ Action: Sam — fix the retry test"
    poster.assert_called_once_with(line)
    assert wake.call_count == 0
    assert llm.call_count == 0
    # Deterministic: same committed delta → identical bytes.
    assert format_chat_line(ev) == format_chat_line(ev)


# --------------------------------------------------------------------------- #
# T-EVENT-08 — AC-EVENT-08: re-fold at same meeting_revision → no duplicate.  #
# --------------------------------------------------------------------------- #
def test_event_08_refold_same_revision_no_duplicate_chat_line() -> None:
    poster = Mock()
    recorder = ChatRecorder(post=poster, enabled=True)
    ev = _final_decision_event(rev=42)

    first = recorder.record(ev)   # posted
    second = recorder.record(ev)  # re-fold of the same delta at revision 42

    assert first is not None
    assert second is None  # dedupe suppressed the second post
    assert poster.call_count == 1
    assert recorder.posts_for_revision(42) == 1


# --------------------------------------------------------------------------- #
# T-EVENT-09 — AC-EVENT-09: disable toggle suppresses all chat posts.         #
# --------------------------------------------------------------------------- #
def test_event_09_disable_toggle_suppresses_all_chat_lines() -> None:
    poster = Mock()
    recorder = ChatRecorder(post=poster, enabled=False)

    events = [
        _final_decision_event(rev=1),
        _action_event(rev=1),
        _final_decision_event(rev=2),
        _action_event(rev=2),
    ]
    posted = record_chat_lines(events, recorder)

    assert posted == []
    assert poster.call_count == 0
    # The formatter itself is still callable (output computed, just not sent).
    assert format_chat_line(events[0]) is not None


# --------------------------------------------------------------------------- #
# T-EVENT-10 — AC-EVENT-10: ContextLine AddOp fires no event; still persisted.#
# --------------------------------------------------------------------------- #
def test_event_10_context_line_only_delta_emits_no_event_but_persists() -> None:
    mock_sink: list[MaterialChangeEvent] = []

    class ListSink:
        def emit(self, event: MaterialChangeEvent) -> None:
            mock_sink.append(event)

    persisted: list[object] = []
    d = delta(AddOp(entry=a_context_line("some chitchat")))

    apply_delta(
        d,
        sink=ListSink(),
        meeting_revision=1,
        persist=lambda op: persisted.append(op),
    )

    # No claim-landed event, no event of any kind (the emitter constraint).
    assert len([e for e in mock_sink if e.kind == MaterialChangeKind.CLAIM_LANDED_CHECKABLE]) == 0
    assert len(mock_sink) == 0
    # The ContextLine op IS persisted (the no-event constraint is about the
    # emitter, not persistence).
    assert len(persisted) == 1
    assert isinstance(persisted[0], AddOp)
    assert isinstance(persisted[0].entry, ContextLine)


def test_event_10_context_line_mixed_with_claim_only_claim_emits() -> None:
    # A delta mixing a context line and a claim emits ONLY the claim event.
    sink = CollectingSink()
    apply_delta(
        delta(
            AddOp(entry=a_context_line("banter")),
            AddOp(entry=a_claim("checkout ships Friday")),
        ),
        sink=sink,
        meeting_revision=1,
    )
    assert [e.kind for e in sink.events] == [MaterialChangeKind.CLAIM_LANDED_CHECKABLE]
