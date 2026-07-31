"""C-CHATFORMAT — the §2.4 chat formatters have a LIVE meeting-runtime caller.

The deterministic chat formatters (``transport.chat.format_note_deltas``) are proven in
isolation by ``tests/doc08/test_chat_formatters.py``. C-CHATFORMAT is the WIRING gap: they
had no live caller, so a committed decision/action/correction note-delta never became a chat
line on a real meeting. This proves the caller exists on the REAL applier path:
``control_plane.scribe_runtime.build_real_seams(..., chat_sink=...).apply_delta`` renders each
COMMITTED note-delta to its §2.4 NoteLine and hands it to the injected chat sink — over the
real ``note_deltas`` ledger on live Postgres (provisioned by ``build/setup-test-env.sh``),
no in-memory substitute for the commit.

The render is deterministic + free + race-free (it rides committed exhaust, never a model
call), and a broken chat sink never aborts the serial notes consumer (Rule 6).
"""
from __future__ import annotations

import os
import uuid

import pytest

from libs.contracts import NoteLine, ProxyMessage
from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.prefix import MeetingHeader
from scribe.schema import (
    ActionItem,
    AddOp,
    Claim,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
    Provenance,
    Reversibility,
)

from control_plane.scribe_runtime import _post_note_deltas_to_chat, build_real_seams

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN,
    reason="integration tier: no TEST_DATABASE_URL (build/setup-test-env.sh provisions it)",
)


async def _db():
    from db.database import Database

    return await Database.connect(_DSN)


def _window(start: float, end: float) -> Window:
    seg = TranscriptSegment(speaker="Ana", text="w", start_s=start, end_s=end, token_count=1)
    return Window(segments=(seg,), boundary_type=BoundaryType.STREAM_END)


def _decision(text: str) -> Decision:
    return Decision(
        text=text,
        status=DecisionStatus.final,
        reversibility=Reversibility.easy,
        leans={"Priya": "for", "Sam": "for"},
    )


def _action(text: str) -> ActionItem:
    return ActionItem(text=text, owner="Sam", due="by Fri", provenance=Provenance.observed)


# ── the pure helper (no DB): a committed delta renders its NoteLine to the sink ─────
def test_post_note_deltas_to_chat_renders_committed_lines() -> None:
    """``_post_note_deltas_to_chat`` renders a committed decision/action delta to NoteLines."""
    posted: list[ProxyMessage] = []
    delta = NoteDelta(
        ops=[AddOp(entry=_decision("ship Friday")), AddOp(entry=_action("fix the retry test"))]
    )
    _post_note_deltas_to_chat(posted.append, delta)

    assert [f.text for f in posted] == [
        "— noted: decision — ship Friday (Priya, Sam agreed)",
        "— action: Sam → fix the retry test (by Fri)",
    ]
    assert all(isinstance(f, NoteLine) for f in posted)


def test_post_note_deltas_to_chat_honors_session_disable() -> None:
    """A disabled note-posting session emits nothing (§2.4 #9)."""
    posted: list[ProxyMessage] = []
    delta = NoteDelta(ops=[AddOp(entry=_decision("ship Friday"))])
    _post_note_deltas_to_chat(posted.append, delta, notes_enabled=False)
    assert posted == []


def test_post_note_deltas_to_chat_swallows_a_broken_sink() -> None:
    """A chat sink that raises never propagates — the notes consumer is never aborted (Rule 6)."""
    def _boom(_frame: object) -> None:
        raise RuntimeError("chat down")

    delta = NoteDelta(ops=[AddOp(entry=_decision("ship Friday"))])
    _post_note_deltas_to_chat(_boom, delta)  # must not raise


# ── the LIVE applier path: a committed note-delta reaches the chat sink on real PG ──
@requires_pg
@pytest.mark.asyncio
async def test_apply_delta_drives_the_chat_formatter_on_commit() -> None:
    """The REAL applier renders each COMMITTED note-delta to a §2.4 NoteLine on the sink."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="chatfmt", participants=("Ana",))
        posted: list[ProxyMessage] = []
        seams = build_real_seams(header, db, chat_sink=posted.append)

        # A committed window carrying a decision AND an action → two §2.4 chat lines.
        await seams.apply_delta(
            meeting_id,
            _window(0.0, 5.0),
            NoteDelta(
                ops=[AddOp(entry=_decision("ship Friday")), AddOp(entry=_action("fix the retry test"))]
            ),
        )

        texts = [f.text for f in posted if isinstance(f, NoteLine)]
        assert "— noted: decision — ship Friday (Priya, Sam agreed)" in texts, texts
        assert "— action: Sam → fix the retry test (by Fri)" in texts, texts
    finally:
        await db.close()


@requires_pg
@pytest.mark.asyncio
async def test_apply_delta_with_no_chat_sink_is_unchanged() -> None:
    """No chat sink → the applier still commits the delta (no behavior change; regression)."""
    from scribe.notes_reader import read_notes

    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="chatfmt-none", participants=("Ana",))
        seams = build_real_seams(header, db)  # no chat_sink

        await seams.apply_delta(
            meeting_id,
            _window(0.0, 5.0),
            NoteDelta(ops=[AddOp(entry=_claim())]),
        )
        folded = await read_notes(meeting_id, db=db)
        assert len(folded.order) == 1, "the delta must still commit with no chat sink wired"
    finally:
        await db.close()


def _claim() -> Claim:
    return Claim(
        text="the API rate limit is 100 rps",
        speaker="Ana",
        said_at_s=1.0,
        firmness=Firmness.hedged,
        provenance=Provenance.observed,
    )
