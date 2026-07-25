"""AC-COAL-15 / -16(-NEG) / -18(-NEG) — the applier proven on REAL Postgres.

The sealed ``test_applier_db.py`` holds these as unconditional ``@_PG_SKIP``
placeholders that never run (their bodies ``raise AssertionError("requires real
Postgres")``). This file is the real oracle for the criteria those placeholders
left un-run — driving the ACTUAL production applier path against a live Postgres
(provisioned by ``build/setup-test-env.sh`` → ``TEST_DATABASE_URL``), never a
stub. It authors ONLY the genuinely-uncovered criteria; the ones already proven
elsewhere are documented at the bottom of this file (not re-authored).

Two production seams are exercised here, each the REAL one:

* ``harness.scribe_runtime.build_real_seams(...).apply_delta`` — the fold-path
  applier that appends to the append-only ``note_deltas`` ledger; the durable
  notes object is the deterministic left-fold of that ledger
  (``scribe.notes_reader.read_notes`` → ``Notes.fold_all``). This is where the
  id-minting / patch-supersede / fact-folding *semantics* of AC-COAL-15/-16 live.
* ``scribe.notes.apply_note_delta`` — the transactional flip+append seam that
  flips ``transcript_segments.status`` ``pending``→``comprehended`` in the SAME
  transaction as the note-delta append. This is the seam AC-COAL-18's re-claim
  idempotency rides: the re-claim scan (``repos.transcript.pending_segment_ids``)
  selects only ``status='pending'`` rows, and the append is ``ON CONFLICT DO
  NOTHING`` — so a pending window is reprocessed exactly once and a comprehended
  window is never reprocessed.

Both seams run against the same real ``note_deltas`` / ``transcript_segments``
tables — no in-memory substitute (the ``mock_boundary`` on these criteria forbids
one: "real db only").
"""
from __future__ import annotations

import os
import uuid

import pytest

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.notes import apply_note_delta
from scribe.notes_reader import read_notes
from scribe.prefix import MeetingHeader
from scribe.schema import (
    AddOp,
    Claim,
    Firmness,
    NoteDelta,
    PatchOp,
    Provenance,
)

from harness.scribe_runtime import build_real_seams

pytestmark = pytest.mark.asyncio

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN,
    reason="integration tier: no TEST_DATABASE_URL (build/setup-test-env.sh provisions it)",
)


async def _db():
    from db.database import Database

    return await Database.connect(_DSN)


def _window(start: float, end: float) -> Window:
    seg = TranscriptSegment(
        speaker="Ana", text="w", start_s=start, end_s=end, token_count=1
    )
    return Window(segments=(seg,), boundary_type=BoundaryType.STREAM_END)


def _claim(text: str, *, firmness: Firmness, said_at_s: float) -> Claim:
    return Claim(
        text=text,
        speaker="Ana",
        said_at_s=said_at_s,
        firmness=firmness,
        provenance=Provenance.observed,
    )


# ───────────────────────────────────────────────────────────────────────────
# AC-COAL-15 — add mints a NEW id; patch supersedes-not-erases the prior value.
#
# The e2e (test_apply_delta_ids_folds) proves add-mints-a-fresh-id and that a
# patch UPDATES the folded value. What it does NOT assert is the load-bearing
# half of AC-COAL-15: the prior value is *superseded, not erased* — i.e. it
# survives verbatim in the durable record. The append-only ``note_deltas``
# ledger IS that mechanism: the original ``add`` row is never mutated or deleted
# by a later ``patch`` (the patch is a SEPARATE row), so the prior value is
# recoverable from the ledger. This oracle asserts exactly that on real PG.
# ───────────────────────────────────────────────────────────────────────────
@requires_pg
async def test_coal_15_add_mints_new_id_patch_supersedes_prior_value_not_erased() -> None:
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="coal-15", participants=("Ana",))
        seams = build_real_seams(header, db)

        # Seed one existing entry E1 (a hedged claim).
        await seams.apply_delta(
            meeting_id,
            _window(0.0, 5.0),
            NoteDelta(ops=[AddOp(entry=_claim("conversion ~2%", firmness=Firmness.hedged, said_at_s=1.0))]),
        )
        after_seed = await read_notes(meeting_id, db=db)
        assert len(after_seed.order) == 1, after_seed.to_serializable()
        e1 = after_seed.order[0]
        assert after_seed.entries[e1]["firmness"] == "hedged"

        # A window carrying an ADD (new content) AND a PATCH (update to E1). The add
        # must mint a NEW id (E2 != E1); the patch must update E1 in place.
        await seams.apply_delta(
            meeting_id,
            _window(5.0, 10.0),
            NoteDelta(
                ops=[
                    AddOp(entry=_claim("latency is 50ms", firmness=Firmness.speculative, said_at_s=6.0)),
                    PatchOp(target_id=e1, changes={"firmness": "firm"}, supersede_reason="Ana confirmed 2%"),
                ]
            ),
        )

        folded = await read_notes(meeting_id, db=db)
        # add minted a new id — entry_count grew by 1 and the new id is unique.
        assert len(folded.order) == 2, folded.to_serializable()
        e2 = next(i for i in folded.order if i != e1)
        assert e2 != e1, "add must mint a NEW entry id, distinct from every existing id"
        # THRESHOLD duplicate_entry_ids_allowed: 0.
        assert len(set(folded.order)) == len(folded.order), "no duplicate entry ids"
        # patch updated E1's value in the folded object.
        assert folded.entries[e1]["firmness"] == "firm", folded.entries[e1]
        assert folded.entries[e1]["text"] == "conversion ~2%"  # identity preserved

        # THRESHOLD prior_values_erased_not_superseded_allowed: 0. The ORIGINAL add
        # row is still in the append-only ledger verbatim (superseded, not erased):
        # the fold's new value comes from the LATER patch row, while the prior value
        # remains recoverable from the earlier add row.
        async with db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT entry_id, op, payload FROM note_deltas "
                "WHERE meeting_id = $1 AND entry_id = $2 ORDER BY id",
                meeting_id,
                e1,
            )
        ops_for_e1 = [r["op"] for r in rows]
        assert ops_for_e1 == ["add", "patch"], ops_for_e1
        import json as _json

        add_payload = _json.loads(rows[0]["payload"]) if isinstance(rows[0]["payload"], str) else rows[0]["payload"]
        # The prior value ("hedged") is preserved in the original add row — the
        # patch superseded it in the fold WITHOUT erasing it from the record.
        assert add_payload["firmness"] == "hedged", add_payload
    finally:
        await db.close()


# ───────────────────────────────────────────────────────────────────────────
# AC-COAL-16 — a fact stated N times folds to ONE entry, not N rows.
#
# The 50-repetition fixture from the criterion, driven through the REAL applier
# on real PG. (The e2e proves 10 repetitions; the criterion pins the 50-rep
# fixture and the "row count for the fact == 1" oracle explicitly.)
# ───────────────────────────────────────────────────────────────────────────
@requires_pg
async def test_coal_16_fact_stated_50_times_folds_to_one_entry() -> None:
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="coal-16", participants=("Ana",))
        seams = build_real_seams(header, db)

        # State the fact once (mints the entry), then patch-restate it 49 more times
        # across 49 successive windows — the fact recurs, but it is ONE claim.
        await seams.apply_delta(
            meeting_id,
            _window(0.0, 2.0),
            NoteDelta(ops=[AddOp(entry=_claim("the API rate limit is 100 rps", firmness=Firmness.hedged, said_at_s=1.0))]),
        )
        fact_id = (await read_notes(meeting_id, db=db)).order[0]
        for k in range(1, 50):
            await seams.apply_delta(
                meeting_id,
                _window(float(k * 2), float(k * 2 + 2)),
                NoteDelta(
                    ops=[
                        PatchOp(
                            target_id=fact_id,
                            changes={"firmness": "firm" if k == 49 else "hedged"},
                            supersede_reason=f"restated in window {k}",
                        )
                    ]
                ),
            )

        folded = await read_notes(meeting_id, db=db)
        # THRESHOLD duplicate_fact_rows_max: 1 — exactly ONE entry for the fact.
        assert len(folded.order) == 1, folded.to_serializable()
        assert folded.order[0] == fact_id
        # The folded value is the last-stated one (firm), not 50 competing rows.
        assert folded.entries[fact_id]["firmness"] == "firm"

        # In the notes OBJECT (the fold), the fact is exactly one entry regardless of
        # how many ledger rows recorded its restatements.
        async with db.acquire() as conn:
            add_rows = await conn.fetchval(
                "SELECT count(*) FROM note_deltas WHERE meeting_id = $1 AND entry_id = $2 AND op = 'add'",
                meeting_id,
                fact_id,
            )
        # THRESHOLD fact_rows_per_repetition_max: 1 — the fact was ADDed exactly once;
        # the 49 restatements are patches on the same id, never new add rows.
        assert add_rows == 1, f"the fact must be added exactly once, got {add_rows} add rows"
    finally:
        await db.close()


@requires_pg
async def test_coal_16neg_two_distinct_facts_stay_two_entries() -> None:
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="coal-16-neg", participants=("Ana",))
        seams = build_real_seams(header, db)

        # Two DISTINCT factual claims, each stated once — patch-in-place must NOT
        # collapse them into one entry.
        await seams.apply_delta(
            meeting_id,
            _window(0.0, 3.0),
            NoteDelta(
                ops=[
                    AddOp(entry=_claim("F1: conversion is 2%", firmness=Firmness.hedged, said_at_s=1.0)),
                    AddOp(entry=_claim("F2: latency is 50ms", firmness=Firmness.hedged, said_at_s=2.0)),
                ]
            ),
        )

        folded = await read_notes(meeting_id, db=db)
        # THRESHOLD distinct_facts_collapsed_to_single_entry_allowed: 0.
        assert len(folded.order) == 2, folded.to_serializable()
        f1, f2 = folded.order
        assert f1 != f2, "distinct facts must have distinct ids"
        texts = {folded.entries[f1]["text"], folded.entries[f2]["text"]}
        assert texts == {"F1: conversion is 2%", "F2: latency is 50ms"}, texts
    finally:
        await db.close()


# ───────────────────────────────────────────────────────────────────────────
# AC-COAL-18 — a pending window is reprocessed exactly once; a second re-claim
# attempt is a no-op (idempotency via the comprehended guard + ON CONFLICT).
#
# Path under test: ``scribe.notes.apply_note_delta`` flips
# transcript_segments.status pending→comprehended in the same tx as the
# note-delta append, and the re-claim scan (``repos.transcript.pending_segment_ids``)
# selects ONLY pending rows. This is the real crash-then-reclaim story: a window
# left 'pending' (its earlier apply crash-rolled-back per AC-COAL-17-NEG) is
# discovered by the re-claim and reprocessed once; a SECOND re-claim finds it
# comprehended and does nothing — no double-count.
# ───────────────────────────────────────────────────────────────────────────
@requires_pg
async def test_coal_18_pending_window_reprocessed_exactly_once_second_is_noop() -> None:
    from db import repos

    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        # A landed-but-not-yet-comprehended segment (status defaults to 'pending').
        async with db.acquire() as conn:
            seg = await repos.notes.insert_segment(
                conn, meeting_id=meeting_id, text="the migration owner is Ana", start_s=1.0, end_s=3.0
            )
        seg_id = seg["id"]
        assert seg["status"] == "pending"

        # ── Re-claim scan discovers the pending window ──────────────────────────
        async with db.acquire() as conn:
            pending = await repos.transcript.pending_segment_ids(conn, meeting_id)
        assert str(seg_id) in pending, "the pending window must be a re-claim candidate"

        # ── First re-claim: reprocess exactly once — apply + flip to comprehended ─
        await apply_note_delta(db, segment_id=seg_id, delta="reclaimed note")

        async with db.acquire() as conn:
            status_1 = await conn.fetchval(
                "SELECT status FROM transcript_segments WHERE id = $1", seg_id
            )
            deltas_1 = await conn.fetchval(
                "SELECT count(*) FROM note_deltas WHERE meeting_id = $1 AND entry_id = $2",
                meeting_id,
                f"seg-{seg_id}",
            )
        assert status_1 == "comprehended", status_1
        # THRESHOLD notes_rows_from_reprocessing_max: 1 — exactly one delta applied.
        assert deltas_1 == 1, f"first re-claim must apply exactly one note delta, got {deltas_1}"

        # ── Second re-claim attempt: a NO-OP (idempotency) ──────────────────────
        # The re-claim scan no longer selects it (it is comprehended, not pending)…
        async with db.acquire() as conn:
            pending_after = await repos.transcript.pending_segment_ids(conn, meeting_id)
        assert str(seg_id) not in pending_after, "a comprehended window must NOT be a re-claim candidate"

        # …and even a forced second apply of the SAME window adds no second row:
        # ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING makes the
        # re-append a silent no-op, so the notes object is not double-counted.
        await apply_note_delta(db, segment_id=seg_id, delta="reclaimed note")
        async with db.acquire() as conn:
            deltas_2 = await conn.fetchval(
                "SELECT count(*) FROM note_deltas WHERE meeting_id = $1 AND entry_id = $2",
                meeting_id,
                f"seg-{seg_id}",
            )
            status_2 = await conn.fetchval(
                "SELECT status FROM transcript_segments WHERE id = $1", seg_id
            )
        # THRESHOLD double_applies_allowed: 0 — the second attempt added no row.
        assert deltas_2 == 1, f"second re-claim must be a no-op; got {deltas_2} delta rows"
        assert status_2 == "comprehended", status_2
    finally:
        await db.close()


@requires_pg
async def test_coal_18neg_comprehended_window_not_in_reclaim_candidate_set() -> None:
    from db import repos

    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        # One comprehended segment and one still-pending segment in the SAME meeting.
        async with db.acquire() as conn:
            done = await repos.notes.insert_segment(
                conn, meeting_id=meeting_id, text="already comprehended", start_s=0.0, end_s=1.0
            )
            todo = await repos.notes.insert_segment(
                conn, meeting_id=meeting_id, text="still pending", start_s=1.0, end_s=2.0
            )
        # Comprehend the first through the real applier seam.
        await apply_note_delta(db, segment_id=done["id"], delta="note")

        async with db.acquire() as conn:
            candidates = await repos.transcript.pending_segment_ids(conn, meeting_id)

        # THRESHOLD comprehended_windows_reprocessed_allowed: 0 — the comprehended
        # window appears ZERO times in the candidate set; only the pending one does.
        assert str(done["id"]) not in candidates, "comprehended window must not be re-claimed"
        assert str(todo["id"]) in candidates, "the pending window must remain a candidate"
        assert candidates.count(str(done["id"])) == 0
    finally:
        await db.close()


# ───────────────────────────────────────────────────────────────────────────
# COVERAGE MAP — what this file does NOT re-author (already proven on the real path):
#
# * AC-COAL-14 / -14-NEG (dropped span → comprehension gap; success → no gap):
#     tests/doc03/coalescer/test_pipeline.py::{test_coal_11,_12,_13} assert the
#     ``mark_gap`` seam is invoked with the span's (start_s, end_s, reason) on a
#     drop and NOT on a success; test_pipeline_real_drop.py drives the REAL
#     scribe_call chain so the drop→gap path fires for the real typed errors. The
#     real ``mark_gap`` DB write (a ``status='gap'`` segment) is the fold/close
#     backfill read (build_real_seams.mark_gap → repos.notes.insert_segment).
# * AC-COAL-15-NEG (single-writer structural): test_applier_db.py::
#     test_coal_15neg_single_writer_structural_no_concurrent_apply_path — a static
#     AST check that runs unconditionally (no DB needed) and passes.
# * AC-COAL-17 / -17-NEG (append+flip in ONE tx; crash mid-apply rolls both back):
#     tests/doc00/test_m03_sub.py AC-SUB-034 drives the REAL apply_note_delta on
#     real PG: a clean apply commits flip+append together; a ``_fail_after_flip``
#     crash rolls BOTH back (status stays 'pending'). That is the exact transaction
#     boundary AC-COAL-17/-17-NEG name.
# ───────────────────────────────────────────────────────────────────────────
