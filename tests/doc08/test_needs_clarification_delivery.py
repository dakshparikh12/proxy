"""A17 · C-CLARIFY — the ``needs_clarification`` envelope maps to the ask-ONE-question path.

05 §358 + CANONICAL §1.2: when the Workroom cannot proceed without one answer it returns a
terminal ``Envelope(status="needs_clarification")`` carrying the ONE question. The harness
done-moment delivery (``harness.workroom_bridge._deliver``) must map that status to the
"ask ONE question through Proxy" delivery — Proxy speaks EXACTLY the single question through
the voice channel, never a result headline + body (two lines is not one question) and never
a staged-draft card. Every other terminal status keeps its headline+detail delivery.

This drives the REAL ``_deliver`` over the REAL ``contracts.Envelope`` through the REAL gated
emitter surface — no mocks of the mapping. Product imports live inside the bodies so the
module collects clean and fails RED if the mapping is absent.
"""
from __future__ import annotations

from uuid import uuid4

from contracts import Envelope


class _FakeEmitter:
    """A gated emitter double recording exactly what reached the wire (is_owner fencing)."""

    def __init__(self, *, is_owner: bool = True) -> None:
        self.is_owner = is_owner
        self.spoken: list[str] = []

    def speak(self, text: object) -> bool:
        if not self.is_owner:
            return False
        self.spoken.append(str(text))
        return True


def test_needs_clarification_speaks_exactly_one_question_from_detail() -> None:
    """A ``needs_clarification`` Envelope speaks the ONE question (its ``detail``), one line."""
    from harness.workroom_bridge import _deliver

    env = Envelope(
        headline="Built: add retry to checkout",  # a producer's speakable — NOT the question
        detail="Which timeout should the retry use — 5s or 30s?",  # the ONE question (D-027)
        status="needs_clarification",
        task_id=uuid4(),
    )
    emitter = _FakeEmitter()
    _deliver(env, emitter)

    assert emitter.spoken == ["Which timeout should the retry use — 5s or 30s?"], (
        "needs_clarification must speak EXACTLY the one question (detail), never headline+body"
    )


def test_needs_clarification_falls_back_to_headline_when_no_detail() -> None:
    """If a producer put the question in the speakable headline, that ONE line is asked."""
    from harness.workroom_bridge import _deliver

    env = Envelope(
        headline="Do you want this on staging or prod?",
        detail=None,
        status="needs_clarification",
        task_id=uuid4(),
    )
    emitter = _FakeEmitter()
    _deliver(env, emitter)

    assert emitter.spoken == ["Do you want this on staging or prod?"]


def test_needs_clarification_fenced_out_emitter_asks_nothing() -> None:
    """A fenced-out (not is_owner) harness delivers no question (Law 3 / §3.7)."""
    from harness.workroom_bridge import _deliver

    env = Envelope(
        headline="q", detail="Which env?", status="needs_clarification", task_id=uuid4()
    )
    emitter = _FakeEmitter(is_owner=False)
    _deliver(env, emitter)

    assert emitter.spoken == []


def test_done_status_still_delivers_headline_then_detail() -> None:
    """A non-clarification terminal status keeps the headline + body delivery (regression)."""
    from harness.workroom_bridge import _deliver

    env = Envelope(
        headline="traced it: 3 call sites",
        detail="checkout.py:42, api.py:7, worker.py:11",
        status="done",
        task_id=uuid4(),
    )
    emitter = _FakeEmitter()
    _deliver(env, emitter)

    assert emitter.spoken == [
        "traced it: 3 call sites",
        "checkout.py:42, api.py:7, worker.py:11",
    ], "a done result keeps its two-line headline+detail delivery"


def test_empty_clarification_asks_nothing_never_fabricates() -> None:
    """A clarification with no question text asks NOTHING — never a fabricated question (Law 1/2)."""
    from harness.workroom_bridge import _deliver

    env = Envelope(headline="", detail=None, status="needs_clarification", task_id=uuid4())
    emitter = _FakeEmitter()
    _deliver(env, emitter)

    assert emitter.spoken == []
