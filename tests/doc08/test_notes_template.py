"""Doc 08 · §2.6 — the notes-file template (the designed artifact forwarded to the VP).

Node ``experience.notes-file-template``. Doc 03's close pass already renders a notes
markdown via ``scribe.close.render_markdown(FinalNotes)`` — but only
title/summary/decisions/action-items/open-questions. This node FIXES that single
renderer to the full §2.6 structure:

    title/date/ATTENDEES
    -> a FIVE-LINE summary with WORST-NEWS-FIRST (any live blocker/risk leads)
    -> decisions each carrying what/who/when AND the moment it landed
    -> action items (who/what/when)
    -> open questions
    -> a 'WHAT PROXY DID' section (each ask handled / work performed / draft staged,
       each with its receipt)
    -> a pointer to the raw transcript.

Rendered DETERMINISTICALLY from the folded ``note_deltas`` object (the same fold
``scribe.notes_reader.Notes`` produces, CANONICAL §11.4). These tests run the REAL
renderer — no mocks — and assert each mandatory section is PRESENT, that the summary
leads worst-news-first, that the receipts ride the 'what Proxy did' section, and that
the render is byte-stable (deterministic). It also asserts the additive change did NOT
break the Doc 03 close render (backwards-compatible defaults) and that no internal
component name (Scribe/workroom/Orchestrator) leaks into the forwarded artifact.
"""
from __future__ import annotations

import re

from scribe.close import (
    FinalActionItem,
    FinalDecision,
    FinalNotes,
    FinalOpenQuestion,
    ProxyAction,
    render_markdown,
)


# ── A fully-populated §2.6 notes object (the artifact forwarded to the VP) ────

def _full_notes() -> FinalNotes:
    return FinalNotes(
        title="Checkout retry review",
        meeting_date="2026-07-25",
        attendees=["Sam Rivera", "Maya Chen", "Priya Patel"],
        blockers_risks=[
            "Payments retry ships behind the rate-limiter change still awaiting approval",
        ],
        summary="The team reviewed the checkout retry logic and agreed on next steps.",
        decisions=[
            FinalDecision(
                text="Adopt exponential backoff for the retry path",
                owner="Sam Rivera",
                when="by Friday",
                landed_moment="landed when Sam confirmed the payments SLA math held",
            ),
        ],
        action_items=[
            FinalActionItem(text="Wire the backoff config", owner="Maya Chen", due="Wed"),
        ],
        open_questions=[
            FinalOpenQuestion(text="Who owns the staged rollout?"),
        ],
        proxy_actions=[
            ProxyAction(
                kind="ask",
                text="Answered: what is the current retry ceiling?",
                receipt="payments/retry.py:88 (✓ resolved)",
            ),
            ProxyAction(
                kind="work",
                text="Built the rate-limiter change",
                receipt="ran 14 tests, all green — receipt gs://ev/meet/cmd-7",
            ),
            ProxyAction(
                kind="draft",
                text="Staged the rate-limiter diff for approval",
                receipt="draft 3f2a — awaiting your click",
            ),
        ],
        transcript_ref="gs://proxy-transcripts/meet-42/raw.jsonl",
    )


# ── The mandatory §2.6 sections are all PRESENT ──────────────────────────────

def test_title_date_attendees_are_present() -> None:
    """§2.6: title/date/ATTENDEES lead the artifact (attendees are NOT optional here)."""
    md = render_markdown(_full_notes())
    assert md.startswith("# Checkout retry review")
    assert "2026-07-25" in md
    # Attendees must be a named section carrying every attendee — the DoD calls out
    # that a missing attendees block is NOT done.
    assert re.search(r"##+\s*Attendees", md), "attendees section absent"
    for name in ("Sam Rivera", "Maya Chen", "Priya Patel"):
        assert name in md, f"attendee {name!r} not rendered"


def test_summary_leads_worst_news_first() -> None:
    """§2.6: any live blocker/risk LEADS the five-line summary — worst news first.

    The blocker/risk text must appear ABOVE the happy summary sentence in the body,
    never buried below the decisions.
    """
    md = render_markdown(_full_notes())
    blocker = "Payments retry ships behind the rate-limiter change still awaiting approval"
    happy = "The team reviewed the checkout retry logic and agreed on next steps."
    assert blocker in md, "blocker/risk not rendered in the summary"
    assert happy in md
    # worst-news-first ORDERING: the blocker precedes the happy summary line.
    assert md.index(blocker) < md.index(happy), "blocker/risk did not LEAD the summary"
    # ...and both precede the decisions section (summary is at the top, §2.6 order).
    assert md.index(blocker) < md.index("Decisions"), "summary block did not lead the decisions"


def test_decisions_carry_what_who_when_and_the_moment_it_landed() -> None:
    """§2.6: each decision carries what / who / when AND the moment it landed."""
    md = render_markdown(_full_notes())
    assert "Adopt exponential backoff for the retry path" in md  # what
    assert "Sam Rivera" in md  # who
    assert "by Friday" in md  # when
    assert "landed when Sam confirmed the payments SLA math held" in md  # the moment it landed


def test_action_items_carry_who_what_when() -> None:
    md = render_markdown(_full_notes())
    assert "Wire the backoff config" in md  # what
    assert "Maya Chen" in md  # who
    assert "Wed" in md  # when


def test_what_proxy_did_section_present_with_a_receipt_per_item() -> None:
    """§2.6 + Law 1 (grounded or silent): the 'what Proxy did' section lists each
    ask handled / work performed / draft staged, and EACH item carries its receipt."""
    md = render_markdown(_full_notes())
    assert re.search(r"##+\s*What Proxy did", md), "'what Proxy did' section absent"
    # every ProxyAction's text AND its receipt render, in order.
    items = [
        ("Answered: what is the current retry ceiling?", "payments/retry.py:88 (✓ resolved)"),
        ("Built the rate-limiter change", "ran 14 tests, all green — receipt gs://ev/meet/cmd-7"),
        ("Staged the rate-limiter diff for approval", "draft 3f2a — awaiting your click"),
    ]
    section = md[md.index("What Proxy did"):]
    for text, receipt in items:
        assert text in section, f"proxy action {text!r} not rendered"
        assert receipt in section, f"receipt {receipt!r} for {text!r} not rendered"
        # the receipt rides WITH its item (same-or-later position, grounded not silent).
        assert section.index(text) <= section.index(receipt)


def test_raw_transcript_pointer_present() -> None:
    """§2.6: a pointer to the raw transcript closes the artifact."""
    md = render_markdown(_full_notes())
    assert "gs://proxy-transcripts/meet-42/raw.jsonl" in md
    assert re.search(r"transcript", md, re.IGNORECASE), "no raw-transcript pointer"


# ── §2.6 top-to-bottom ORDER (scannable in 30s, complete in 5min) ────────────

def test_section_order_matches_2_6() -> None:
    """§2.6 order: title/attendees -> summary -> decisions -> action items ->
    open questions -> what Proxy did -> transcript pointer."""
    md = render_markdown(_full_notes())
    order = [
        "# Checkout retry review",
        "Attendees",
        "Summary",
        "Decisions",
        "Action items",
        "Open questions",
        "What Proxy did",
        "Raw transcript",
    ]
    positions = [md.index(tok) for tok in order]
    assert positions == sorted(positions), f"§2.6 section order violated: {positions}"


# ── Determinism (the DoD forbids a non-deterministic render) ──────────────────

def test_render_is_deterministic() -> None:
    """The exact bytes are identical across renders — no clock, no set iteration,
    no dict-order dependence (the DoD: NOT done if the render is non-deterministic)."""
    notes = _full_notes()
    assert render_markdown(notes) == render_markdown(notes)


# ── The forwarded artifact never leaks an internal component name ─────────────

def test_no_internal_component_name_leaks() -> None:
    """Hard rule: user-visible strings never carry Scribe/workroom/Orchestrator."""
    md = render_markdown(_full_notes())
    assert not re.search(r"\b(scribe|workroom|orchestrator)\b", md, re.IGNORECASE)


# ── Backwards compatibility: the Doc 03 close render is unchanged ─────────────

def test_backwards_compatible_default_render_unchanged() -> None:
    """A FinalNotes built the OLD way (no §2.6 fields) renders EXACTLY the Doc 03
    bytes — the additive extension does not break the sealed close render."""
    notes = FinalNotes(
        summary="  hello  ",
        decisions=[FinalDecision(text="ship it")],
        action_items=[FinalActionItem(text="write tests", owner="dp", due="fri")],
        open_questions=[FinalOpenQuestion(text="who owns rollout?")],
    )
    md = render_markdown(notes)
    assert md == (
        "# Meeting notes\n\n"
        "## Summary\n\n"
        "hello\n\n"
        "## Decisions\n\n"
        "- ship it\n\n"
        "## Action items\n\n"
        "- [ ] write tests — dp (fri)\n\n"
        "## Open questions\n\n"
        "- who owns rollout?\n"
    )


def test_empty_notes_still_render_placeholders() -> None:
    """The old empty-section placeholders survive (no attendees/what-proxy-did blocks
    are fabricated for an empty object)."""
    md = render_markdown(FinalNotes(summary="s"))
    assert "_None recorded._" in md
    assert "_None._" in md
    assert "Attendees" not in md  # no attendees -> no attendees section fabricated
    assert "What Proxy did" not in md


# ── Deterministic build FROM the folded note_deltas object ────────────────────

def test_final_notes_from_folded_note_deltas_is_deterministic() -> None:
    """The §2.6 object is built DETERMINISTICALLY from the folded note_deltas object
    (CANONICAL §11.4). Same fold in -> byte-identical markdown out, twice."""
    from scribe.close import final_notes_from_folded
    from scribe.notes_reader import Notes

    deltas = [
        {"entry_id": "d1", "op": "add", "created_at": "2026-07-25T10:00:00Z",
         "payload": {"kind": "decision", "text": "Adopt backoff",
                     "leans": {"Sam": "for"}, "status": "final"}},
        {"entry_id": "a1", "op": "add", "created_at": "2026-07-25T10:01:00Z",
         "payload": {"kind": "action", "text": "Wire config", "owner": "Maya", "due": "Wed"}},
        {"entry_id": "q1", "op": "add", "created_at": "2026-07-25T10:02:00Z",
         "payload": {"kind": "open_question", "text": "Who owns rollout?"}},
    ]
    folded = Notes.fold_all(deltas)
    n1 = final_notes_from_folded(folded)
    n2 = final_notes_from_folded(folded)
    assert isinstance(n1, FinalNotes)
    md1 = render_markdown(n1)
    md2 = render_markdown(n2)
    assert md1 == md2  # deterministic from the folded object
    assert "Adopt backoff" in md1
    assert "Wire config" in md1
    assert "Who owns rollout?" in md1
