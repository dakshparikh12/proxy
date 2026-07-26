"""Doc 08 · §2.4 #3/#4/#8/#14 — the DETERMINISTIC chat formatters.

Node ``experience.chat-formatters``. The one template set of deterministic chat
formatters — each riding existing exhaust, NEVER model-generated (so free, race-free,
cannot hallucinate a decision). They emit **registered** ``NoteLine`` / ``DraftCard``
render frames (``libs.contracts``), NOT prose the model produced:

  * §2.4 #3 — the decision/action note-lines are a deterministic harness formatter
    keyed on a COMMITTED note-delta (CANONICAL §12.12): only a committed
    ``AddOp`` carrying a ``Decision`` / ``ActionItem`` entry emits a line —
      "— noted: decision — ship Friday (Priya, Sam agreed)"
      "— action: Sam → fix the retry test (by Fri)"
    — never spoken, never a wake, honoring the session disable ("stop posting
    decision notes").
  * §2.4 #4 — the correction ack rides Doc 03's live notes patch (a ``PatchOp``):
      "— corrected: ship Friday"
  * §2.4 #8 — the draft card renders what-it-is → one-line change summary → the
    ``/m/{meeting_id}`` link (NEVER a raw GCS URI) → "approve = your click",
    carrying the persisted ``Envelope.draft_id`` (CANONICAL §11.5).
  * §2.4 #14 — the reconciliation card is a render VARIANT: two values side by side,
    each with its as-of source and date.
  * §2.3 — the reconnect summary is the honest gap line, rendered as a NoteLine.

Oracle strategy (PROTO-DETERMINISTIC-01): the tests drive the REAL formatter path
(``transport.chat``) over REAL committed scribe ``NoteDelta`` ops (the live
``scribe.schema`` models) and a REAL ``Envelope`` (``libs.contracts``), and assert on
REAL registered ``ProxyMessage`` frames. No mocks. Product imports live inside the
test bodies so this module COLLECTS clean and FAILS RED before the formatters exist.
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from libs.contracts import (
    CHANNEL_REGISTRY,
    DraftCard,
    Envelope,
    NoteLine,
    ProxyMessage,
    assert_registry_closed,
)
from scribe.schema import (
    ActionItem,
    AddOp,
    CloseOp,
    Decision,
    DecisionStatus,
    NoteDelta,
    PatchOp,
    Provenance,
    Reversibility,
)


# ── helpers: build REAL committed note-deltas (the live scribe schema) ────────────
def _decision(text: str, leans: dict[str, str] | None = None) -> Decision:
    return Decision(
        text=text,
        status=DecisionStatus.final,
        reversibility=Reversibility.easy,
        leans=leans or {},
    )


def _action(text: str, owner: str | None = None, due: str | None = None) -> ActionItem:
    return ActionItem(text=text, owner=owner, due=due, provenance=Provenance.observed)


# ── §2.4 #3 · the decision note-line — deterministic, keyed on a committed delta ───
def test_committed_decision_delta_emits_deterministic_note_line() -> None:
    """A committed ``AddOp(Decision)`` emits the exact decision NoteLine (§2.4 #3)."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(ops=[AddOp(entry=_decision("ship Friday", {"Priya": "for", "Sam": "for"}))])

    frames = list(format_note_deltas(delta, committed=True))

    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame, NoteLine)
    assert isinstance(frame, ProxyMessage)  # a REGISTERED instance, never a bare dict
    assert frame.text == "— noted: decision — ship Friday (Priya, Sam agreed)"


def test_committed_action_delta_emits_deterministic_note_line() -> None:
    """A committed ``AddOp(ActionItem)`` emits the exact action NoteLine (§2.4 #3)."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(ops=[AddOp(entry=_action("fix the retry test", owner="Sam", due="by Fri"))])

    frames = list(format_note_deltas(delta, committed=True))

    assert len(frames) == 1
    assert isinstance(frames[0], NoteLine)
    assert frames[0].text == "— action: Sam → fix the retry test (by Fri)"


def test_decision_line_is_deterministic_across_repeated_calls() -> None:
    """The SAME committed delta yields BYTE-IDENTICAL frames every time (determinism).

    NOT done if formatting is non-deterministic — so the same input must produce the
    same output on repeated calls (no dict-ordering / timestamp / randomness leak).
    """
    from transport.chat import format_note_deltas

    delta = NoteDelta(
        ops=[
            AddOp(entry=_decision("ship Friday", {"Priya": "for", "Sam": "for", "Dana": "against"})),
            AddOp(entry=_action("fix the retry test", owner="Sam", due="by Fri")),
        ]
    )

    first = [f.text for f in format_note_deltas(delta, committed=True)]
    second = [f.text for f in format_note_deltas(delta, committed=True)]
    third = [f.text for f in format_note_deltas(delta, committed=True)]
    assert first == second == third
    assert len(first) == 2  # one line per committed add op


# ── uncommitted state emits NOTHING (never a line from uncommitted state) ─────────
def test_uncommitted_delta_emits_no_line() -> None:
    """An UNCOMMITTED delta emits NOTHING — a line rides ONLY committed exhaust.

    NOT done if a line is emitted from uncommitted state (the DoD's explicit failure
    mode) — the formatter is keyed on a COMMITTED note-delta (CANONICAL §12.12).
    """
    from transport.chat import format_note_deltas

    delta = NoteDelta(ops=[AddOp(entry=_decision("ship Friday", {"Priya": "for"}))])

    assert list(format_note_deltas(delta, committed=False)) == []


# ── the session disable ("stop posting decision notes", §2.4 #9) is honored ───────
def test_session_disable_suppresses_decision_and_action_lines() -> None:
    """When the session disabled note-posting, a committed delta emits NO line (§2.4 #9)."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(
        ops=[
            AddOp(entry=_decision("ship Friday", {"Priya": "for"})),
            AddOp(entry=_action("fix the retry test", owner="Sam", due="by Fri")),
        ]
    )

    assert list(format_note_deltas(delta, committed=True, notes_enabled=False)) == []


# ── non-decision/action adds do NOT emit a line (only #3's two entry kinds) ───────
def test_claim_or_context_add_emits_no_line() -> None:
    """Only Decision/ActionItem adds are §2.4 #3 note-lines; a Claim/Context add is silent."""
    from transport.chat import format_note_deltas
    from scribe.schema import ContextLine

    delta = NoteDelta(ops=[AddOp(entry=ContextLine(text="some side chatter"))])

    assert list(format_note_deltas(delta, committed=True)) == []


# ── §2.4 #4 · the correction ack rides a committed notes PATCH ────────────────────
def test_committed_patch_emits_correction_ack() -> None:
    """A committed ``PatchOp`` (Doc 03 live notes patch) emits the correction ack (§2.4 #4)."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(
        ops=[
            PatchOp(
                target_id="d1",
                changes={"text": "ship Friday"},
                supersede_reason="participant corrected Monday→Friday",
            )
        ]
    )

    frames = list(format_note_deltas(delta, committed=True))

    assert len(frames) == 1
    assert isinstance(frames[0], NoteLine)
    assert frames[0].text == "— corrected: ship Friday"


def test_uncommitted_patch_emits_no_correction_ack() -> None:
    """An uncommitted patch emits no correction ack — committed exhaust only."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(
        ops=[PatchOp(target_id="d1", changes={"text": "ship Friday"}, supersede_reason="x")]
    )
    assert list(format_note_deltas(delta, committed=False)) == []


def test_close_op_emits_no_line() -> None:
    """A ``CloseOp`` is not one of §2.4's chat lines — it emits nothing (scoped set)."""
    from transport.chat import format_note_deltas

    delta = NoteDelta(ops=[CloseOp(target_id="q1", resolution="answered: 3.4%")])
    assert list(format_note_deltas(delta, committed=True)) == []


# ── §2.4 #8 · the draft card — /m/ link + Envelope.draft_id, never a raw GCS URI ──
def test_draft_card_carries_m_link_and_draft_id() -> None:
    """The draft card renders what/change/─//m/ link/'your click' + Envelope.draft_id (§2.4 #8)."""
    from transport.chat import format_draft_card

    meeting_id = uuid4()
    draft_id = uuid4()
    envelope = Envelope(
        headline="patch the retry backoff",
        detail="switch the fixed 100ms sleep to exponential backoff",
        status="needs_review",
        draft_id=draft_id,
        task_id=uuid4(),
    )

    frame = format_draft_card(envelope, meeting_id=meeting_id)

    assert isinstance(frame, DraftCard)
    assert isinstance(frame, ProxyMessage)
    # carries the PERSISTED Envelope.draft_id (the /m/ accept route reads the same field).
    assert frame.draft_id == draft_id
    # the card body: what-it-is → one-line change → the /m/ link → "your click".
    summary = frame.summary
    assert f"/m/{meeting_id}" in summary  # the authenticated home link
    assert "gs://" not in summary and "https://storage.googleapis" not in summary  # NEVER a raw GCS URI
    assert "your click" in summary  # approve = your click
    assert "patch the retry backoff" in summary  # what it is
    assert "exponential backoff" in summary  # the one-line change summary


def test_draft_card_link_is_never_a_raw_gcs_uri_even_with_gcs_artifact() -> None:
    """Even when the envelope's artifact holds a gs:// ref, the card link stays /m/.

    NOT done if the draft-card link is a raw GCS URI — the link ALWAYS points at the
    authenticated ``/m/{meeting_id}`` home (§2.4 #8, §2.8), never the object store.
    """
    from transport.chat import format_draft_card

    meeting_id = uuid4()
    draft_id = uuid4()
    envelope = Envelope(
        headline="update the config",
        detail="raise the pool size to 8",
        artifact={"gcs_uri": "gs://proxy-drafts/abc123.diff"},
        status="needs_review",
        draft_id=draft_id,
        task_id=uuid4(),
    )

    frame = format_draft_card(envelope, meeting_id=meeting_id)

    assert "gs://" not in frame.summary
    assert f"/m/{meeting_id}" in frame.summary


def test_draft_card_without_draft_id_is_a_wiring_error() -> None:
    """An envelope with no persisted ``draft_id`` cannot render a draft card.

    NOT done if a draft card omits the draft_id — so a draft_id-less envelope must
    RAISE, never render a card that points a click at nothing.
    """
    from transport.chat import format_draft_card

    envelope = Envelope(headline="x", status="needs_review", draft_id=None, task_id=uuid4())

    with pytest.raises((ValueError, AssertionError)):
        format_draft_card(envelope, meeting_id=uuid4())


def test_draft_card_is_deterministic() -> None:
    """The SAME envelope + meeting renders a BYTE-IDENTICAL card (determinism)."""
    from transport.chat import format_draft_card

    meeting_id = uuid4()
    envelope = Envelope(
        headline="patch the retry backoff",
        detail="switch to exponential backoff",
        status="needs_review",
        draft_id=uuid4(),
        task_id=uuid4(),
    )
    a = format_draft_card(envelope, meeting_id=meeting_id)
    b = format_draft_card(envelope, meeting_id=meeting_id)
    assert a.model_dump() == b.model_dump()


# ── §2.4 #14 · the reconciliation card — two as-of-sourced values side by side ────
def test_reconciliation_card_shows_two_sourced_values_side_by_side() -> None:
    """The reconciliation card posts two values, each with its as-of source + date (§2.4 #14)."""
    from transport.chat import format_reconciliation_card

    frame = format_reconciliation_card(
        left_value="$2.4M",
        left_source="deck",
        left_as_of=None,
        right_value="$2.19M",
        right_source="Q3 filing",
        right_as_of="as of Q3 close",
    )

    assert isinstance(frame, NoteLine)
    assert isinstance(frame, ProxyMessage)
    text = frame.text
    # both values present, each tied to its source; the as-of date is carried.
    assert "$2.4M" in text and "deck" in text
    assert "$2.19M" in text and "Q3 filing" in text
    assert "Q3 close" in text
    # a sourced comparison, NOT a "you're wrong" verdict (no accusatory verb).
    assert "wrong" not in text.lower()


def test_reconciliation_card_is_deterministic() -> None:
    """The same two sourced values render a byte-identical card (determinism)."""
    from transport.chat import format_reconciliation_card

    kw = dict(
        left_value="$2.4M",
        left_source="deck",
        left_as_of=None,
        right_value="$2.19M",
        right_source="Q3 filing",
        right_as_of="as of Q3 close",
    )
    assert format_reconciliation_card(**kw).text == format_reconciliation_card(**kw).text


# ── §2.3 · the reconnect summary — the honest gap, as a registered NoteLine ───────
def test_reconnect_summary_renders_the_honest_gap_as_a_note_line() -> None:
    """The reconnect summary renders the real disconnect window as a NoteLine (§2.3).

    It rides the existing ``failure.Gap`` wording (one source for the gap line) and
    surfaces it as a registered render frame — honest, sourced, never pretending
    continuity.
    """
    from transport.chat import format_reconnect_summary
    from transport.failure import Gap

    gap = Gap(dropped_ts=843.0, rejoined_ts=884.0)
    frame = format_reconnect_summary(gap)

    assert isinstance(frame, NoteLine)
    assert frame.text == gap.line()  # ONE source for the gap line (no re-wording)
    assert "gap" in frame.text.lower()


# ── every emitted frame is a REGISTERED ProxyMessage; registry stays closed ───────
def test_all_formatter_frames_are_registered_and_registry_closed() -> None:
    """Every frame the formatters emit is a registered ProxyMessage; closure stays green.

    NOT done unless the frames are registered ProxyMessage instances
    (``assert_registry_closed`` stays green).
    """
    from transport.chat import (
        format_draft_card,
        format_note_deltas,
        format_reconciliation_card,
        format_reconnect_summary,
    )
    from transport.failure import Gap

    frames: list[ProxyMessage] = []
    frames += list(
        format_note_deltas(
            NoteDelta(
                ops=[
                    AddOp(entry=_decision("ship Friday", {"Priya": "for"})),
                    AddOp(entry=_action("fix the retry test", owner="Sam", due="by Fri")),
                    PatchOp(target_id="d1", changes={"text": "ship Friday"}, supersede_reason="x"),
                ]
            ),
            committed=True,
        )
    )
    frames.append(
        format_draft_card(
            Envelope(headline="x", detail="y", status="needs_review", draft_id=uuid4(), task_id=uuid4()),
            meeting_id=uuid4(),
        )
    )
    frames.append(
        format_reconciliation_card(
            left_value="$2.4M", left_source="deck", left_as_of=None,
            right_value="$2.19M", right_source="Q3 filing", right_as_of="as of Q3 close",
        )
    )
    frames.append(format_reconnect_summary(Gap(dropped_ts=843.0, rejoined_ts=884.0)))

    for frame in frames:
        assert isinstance(frame, ProxyMessage)
        assert frame.type in CHANNEL_REGISTRY
        # round-trips through the registered model (a real model_dump/validate).
        CHANNEL_REGISTRY[frame.type].model_validate(frame.model_dump(mode="json"))

    assert_registry_closed()  # must not raise — the frames are all already-registered types


# ── copy voice: no internal names, no filler/exclamation (§2.1 + naming lint) ─────
def test_formatter_strings_pass_the_naming_lint_and_copy_guide() -> None:
    """Every emitted user-visible string passes the naming lint + the copy guide (§2.1)."""
    from lint.copy_guide import check_copy
    from lint.naming import check_user_visible_strings
    from transport.chat import (
        format_draft_card,
        format_note_deltas,
        format_reconciliation_card,
        format_reconnect_summary,
    )
    from transport.failure import Gap

    strings: dict[str, str] = {}
    for i, frame in enumerate(
        format_note_deltas(
            NoteDelta(
                ops=[
                    AddOp(entry=_decision("ship Friday", {"Priya": "for", "Sam": "for"})),
                    AddOp(entry=_action("fix the retry test", owner="Sam", due="by Fri")),
                    PatchOp(target_id="d1", changes={"text": "ship Friday"}, supersede_reason="x"),
                ]
            ),
            committed=True,
        )
    ):
        strings[f"note.{i}"] = frame.text
    strings["draft"] = format_draft_card(
        Envelope(headline="patch the retry backoff", detail="switch to exponential backoff",
                 status="needs_review", draft_id=uuid4(), task_id=uuid4()),
        meeting_id=uuid4(),
    ).summary
    strings["reconcile"] = format_reconciliation_card(
        left_value="$2.4M", left_source="deck", left_as_of=None,
        right_value="$2.19M", right_source="Q3 filing", right_as_of="as of Q3 close",
    ).text
    strings["reconnect"] = format_reconnect_summary(Gap(dropped_ts=843.0, rejoined_ts=884.0)).text

    naming = check_user_visible_strings(strings)
    assert naming.exit_code == 0, f"formatter strings leak an internal name: {naming.violations!r}"
    copy = check_copy(strings)
    assert copy.exit_code == 0, f"formatter strings trip the copy guide: {copy.violations!r}"


# ── the formatters are NOT model-generated: no LLM / provider call on the path ────
def _code_only(fn: object) -> str:
    """The function source with comments AND string/docstring literals stripped.

    So the guard tests the actual CODE (calls, attribute access), never the docstring
    prose that legitimately NAMES the anti-pattern to explain the code avoids it.
    Mirrors ``tests/doc08/test_channel_projector.py``'s ``_code_only``.
    """
    import io
    import textwrap
    import tokenize

    src = textwrap.dedent(inspect.getsource(fn))  # type: ignore[arg-type]
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out).lower()


def test_formatters_are_deterministic_code_not_model_generated() -> None:
    """The formatter CODE never calls an LLM/provider — it is a deterministic formatter.

    NOT done if a line is model-generated: the formatters must be pure code keyed on
    the committed delta, never a wake/provider round-trip (CANONICAL §12.12). Checks
    the CODE (docstrings that name the anti-pattern to explain the avoidance are
    stripped first).
    """
    import transport.chat as chat_mod

    for fn_name in (
        "format_note_deltas",
        "format_draft_card",
        "format_reconciliation_card",
        "format_reconnect_summary",
    ):
        code = _code_only(getattr(chat_mod, fn_name))
        for forbidden in ("call_external", "stream_deltas", "provider", "generate", "completion", "anthropic"):
            assert forbidden not in code, (
                f"{fn_name} must be a deterministic formatter — found {forbidden!r} (model-generated?)"
            )
