"""AC-COAL-APPLY-IDS — the PRODUCTION applier writes deltas the reader can fold.

Gap DOC03-APPLY-IDS-BROKEN: ``harness.scribe_runtime.build_real_seams.apply_delta``
was the SOLE production notes applier, yet it wrote every op under a fabricated
per-op ``entry_id`` (``f"w{window.start_s}-{j}"``) and dumped the WHOLE op as the
payload — dropping ``PatchOp.target_id`` / ``CloseOp.target_id`` entirely. The
canonical fold (``scribe.notes_reader.Notes.fold_all``) keys every op on
``entry_id`` and expects the add payload to BE the entry fields, so a
add(hedged)->patch(firm)->close over two windows folded into THREE garbage
phantom entries instead of ONE claim promoted hedged->firm and resolved.

These tests drive the REAL production seam (``build_real_seams(header, db)``) against
the REAL Postgres ``note_deltas`` table, then fold with the REAL read-path
(``read_notes`` -> ``Notes.fold_all``) — the exact bytes ``GET /internal/notes`` and
``/m/{id}`` return to the room. No double is injected: the applier, the ledger, and
the fold are all production.
"""
from __future__ import annotations

import os
import uuid

import pytest

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.notes_reader import Notes, read_notes
from scribe.prefix import MeetingHeader
from scribe.schema import (
    AddOp,
    Claim,
    CloseOp,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
    OpenQuestion,
    PatchOp,
    Provenance,
    Reversibility,
)

from harness.scribe_runtime import build_real_seams

pytestmark = pytest.mark.asyncio

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN,
    reason="integration tier: no TEST_DATABASE_URL (root conftest auto-provisions :55432)",
)


async def _db():
    from db.database import Database

    return await Database.connect(_DSN)


def _window(start: float, end: float) -> Window:
    seg = TranscriptSegment(
        speaker="Ana", text="w", start_s=start, end_s=end, token_count=1
    )
    return Window(segments=(seg,), boundary_type=BoundaryType.STREAM_END)


@requires_pg
async def test_add_patch_close_over_two_windows_folds_to_one_promoted_resolved_claim() -> None:
    """add(claim hedged)->patch(firm)->close folds to ONE claim, promoted + resolved."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="apply-ids", participants=("Ana",))
        seams = build_real_seams(header, db)

        add_delta = NoteDelta(
            ops=[
                AddOp(
                    entry=Claim(
                        text="maybe 2% conversion",
                        speaker="Ana",
                        said_at_s=1.0,
                        firmness=Firmness.hedged,
                        provenance=Provenance.observed,
                    )
                )
            ],
            current_goal="pin the conversion number",
        )
        await seams.apply_delta(meeting_id, _window(0.0, 5.0), add_delta)

        notes_after_add: Notes = await read_notes(meeting_id, db=db)
        assert len(notes_after_add.order) == 1, notes_after_add.to_serializable()
        claim_id = notes_after_add.order[0]
        assert notes_after_add.entries[claim_id]["firmness"] == "hedged"
        assert notes_after_add.entries[claim_id]["text"] == "maybe 2% conversion"
        assert "op" not in notes_after_add.entries[claim_id]
        assert "entry" not in notes_after_add.entries[claim_id]
        assert notes_after_add.current_goal == "pin the conversion number"

        patch_close = NoteDelta(
            ops=[
                PatchOp(
                    target_id=claim_id,
                    changes={"firmness": "firm"},
                    supersede_reason="confirmed by Ana",
                ),
                CloseOp(target_id=claim_id, resolution="agreed 2%"),
            ]
        )
        await seams.apply_delta(meeting_id, _window(5.0, 10.0), patch_close)

        folded: Notes = await read_notes(meeting_id, db=db)
        assert len(folded.order) == 1, folded.to_serializable()
        entry = folded.entries[folded.order[0]]
        assert entry["firmness"] == "firm", entry
        assert entry["resolved"] is True, entry
        assert entry["resolution"] == "agreed 2%", entry
        assert entry["text"] == "maybe 2% conversion"
    finally:
        await db.close()


@requires_pg
async def test_decision_forming_to_final_binds_and_question_close_resolves() -> None:
    """A decision forming->final patch binds; an open-question close resolves it."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="bind")
        seams = build_real_seams(header, db)

        await seams.apply_delta(
            meeting_id,
            _window(0.0, 4.0),
            NoteDelta(
                ops=[
                    AddOp(
                        entry=Decision(
                            text="adopt retry backoff",
                            status=DecisionStatus.forming,
                            reversibility=Reversibility.easy,
                        )
                    ),
                    AddOp(
                        entry=OpenQuestion(
                            text="who owns the migration?",
                            raised_by="Ana",
                        )
                    ),
                ]
            ),
        )
        notes = await read_notes(meeting_id, db=db)
        assert len(notes.order) == 2, notes.to_serializable()
        decision_id = next(
            i for i in notes.order if notes.entries[i].get("kind") == "decision"
        )
        question_id = next(
            i for i in notes.order if notes.entries[i].get("kind") == "open_question"
        )
        assert decision_id != question_id

        await seams.apply_delta(
            meeting_id,
            _window(4.0, 8.0),
            NoteDelta(
                ops=[
                    PatchOp(
                        target_id=decision_id,
                        changes={"status": "final"},
                        supersede_reason="ratified",
                    ),
                    CloseOp(target_id=question_id, resolution="Ana owns it"),
                ]
            ),
        )
        folded = await read_notes(meeting_id, db=db)
        assert len(folded.order) == 2, folded.to_serializable()
        assert folded.entries[decision_id]["status"] == "final"
        assert folded.entries[question_id]["resolved"] is True
        assert folded.entries[question_id]["resolution"] == "Ana owns it"
    finally:
        await db.close()


@requires_pg
async def test_fact_said_many_times_is_one_patched_entry_not_many() -> None:
    """A fact patched repeatedly stays ONE entry (object grows with content, not time)."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="one-entry")
        seams = build_real_seams(header, db)

        await seams.apply_delta(
            meeting_id,
            _window(0.0, 2.0),
            NoteDelta(
                ops=[
                    AddOp(
                        entry=Claim(
                            text="latency is 50ms",
                            speaker="Zed",
                            said_at_s=1.0,
                            firmness=Firmness.speculative,
                            provenance=Provenance.observed,
                        )
                    )
                ]
            ),
        )
        cid = (await read_notes(meeting_id, db=db)).order[0]

        for k in range(1, 11):
            await seams.apply_delta(
                meeting_id,
                _window(float(k * 2), float(k * 2 + 2)),
                NoteDelta(
                    ops=[
                        PatchOp(
                            target_id=cid,
                            changes={"firmness": "firm" if k == 10 else "hedged"},
                            supersede_reason=f"restated at window {k}",
                        )
                    ]
                ),
            )

        folded = await read_notes(meeting_id, db=db)
        assert len(folded.order) == 1, folded.to_serializable()
        assert folded.entries[cid]["firmness"] == "firm"
    finally:
        await db.close()
