"""AC-REFM-06 / AC-FAIL-07 / AC-FAIL-07-NEG — the PRODUCTION apply path binds referents.

Gap DOC03-REFERENT-MATCHER-UNWIRED: ``scribe.referent.lookup_referent`` (the
deterministic, no-LLM matcher over overview areas + ``graph_nodes``) was NEVER
invoked in the production apply path. ``build_real_seams.apply_delta`` persisted the
delta ops verbatim (``entry.model_dump``) and never bound ``Claim``/``Decision``
referents to code nodes, so every referent stayed an unbound plain string even when
the term matched a real code area — and the cross-service read ``/internal/notes``
(consumed by the Workroom, Doc 05) carried named-but-unbound referents.

These tests drive the REAL production seam (``build_real_seams(header, db,
referent_corpus=...)``) against the REAL Postgres ``note_deltas`` table with a REAL
on-disk SQLite ``graph_nodes`` corpus, then fold with the REAL read-path
(``read_notes`` -> ``Notes.fold_all``) — the exact bytes the Workroom reads. No
double is injected: the applier, the deterministic matcher, the ledger, and the fold
are all production.

  * AC-FAIL-07-NEG: a resolvable referent (``checkout``) is bound — the folded entry
    carries ``binding_status == 'bound'`` and a resolved binding pointing at the real
    node id from the corpus (never fabricated).
  * AC-FAIL-07 / AC-REFM-05: an unresolvable referent (``the checkout flow``) stays
    named-but-unbound — present, original name kept, ``binding_status == 'unbound'``,
    no fabricated path.
  * AC-REFM-06: the binding survives the fold + read-back verbatim (not stripped).
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.notes_reader import Notes, read_notes
from scribe.prefix import MeetingHeader
from scribe.referent import OverviewArea, ReferentCorpus
from scribe.schema import (
    AddOp,
    Claim,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
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

# The spec's worked example (AC-REFM-04): an area "payments/checkout" + a file
# "checkout.py" with the "checkout" symbol. A REAL on-disk SQLite corpus (SQLite is
# a file, no server), the same shape ``code_intel.graph_store`` writes per-repo.
_SEED_ROWS = [
    ("payments/checkout.py::checkout", "payments/checkout", "payments/checkout.py", "checkout"),
    ("payments/refund.py::issue_refund", "payments/refund", "payments/refund.py", "issue_refund"),
]
_AREAS = (OverviewArea(name="payments/checkout"), OverviewArea(name="auth"))


def _make_corpus(tmp_path: Path) -> ReferentCorpus:
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE graph_nodes (node_id TEXT PRIMARY KEY, area TEXT, file TEXT, symbol TEXT)"
        )
        conn.executemany(
            "INSERT INTO graph_nodes (node_id, area, file, symbol) VALUES (?, ?, ?, ?)",
            _SEED_ROWS,
        )
        conn.commit()
    finally:
        conn.close()
    return ReferentCorpus(areas=_AREAS, db_path=str(db_path))


async def _db():
    from db.database import Database

    return await Database.connect(_DSN)


def _window(start: float, end: float) -> Window:
    seg = TranscriptSegment(speaker="Ana", text="w", start_s=start, end_s=end, token_count=1)
    return Window(segments=(seg,), boundary_type=BoundaryType.STREAM_END)


def _referent_bindings(entry: dict) -> dict:
    """The production carrier the fold surfaces: term -> {binding, binding_status}."""
    rb = entry.get("referent_bindings")
    assert isinstance(rb, dict), f"expected referent_bindings dict on entry, got {entry!r}"
    return rb


@requires_pg
async def test_resolvable_referent_bound_unresolvable_stays_unbound(tmp_path: Path) -> None:
    """A claim carrying 'checkout' (resolvable) + 'the checkout flow' (not) binds honestly."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="referents", participants=("Ana",))
        seams = build_real_seams(header, db, referent_corpus=_make_corpus(tmp_path))

        delta = NoteDelta(
            ops=[
                AddOp(
                    entry=Claim(
                        text="the checkout flow is slow at peak",
                        speaker="Ana",
                        said_at_s=1.0,
                        firmness=Firmness.firm,
                        provenance=Provenance.observed,
                        referents=["checkout", "the checkout flow"],
                    )
                )
            ]
        )
        await seams.apply_delta(meeting_id, _window(0.0, 5.0), delta)

        notes: Notes = await read_notes(meeting_id, db=db)
        assert len(notes.order) == 1, notes.to_serializable()
        entry = notes.entries[notes.order[0]]

        # The original referent names are never dropped (AC-FAIL-07).
        assert set(entry["referents"]) == {"checkout", "the checkout flow"}, entry

        bindings = _referent_bindings(entry)

        # AC-FAIL-07-NEG: 'checkout' binds to the REAL node id from the corpus.
        bound = bindings["checkout"]
        assert bound["binding_status"] == "bound", bound
        assert bound["binding"] == "payments/checkout.py::checkout", bound

        # AC-FAIL-07 / AC-REFM-05: 'the checkout flow' stays named-but-unbound,
        # no fabricated path.
        unbound = bindings["the checkout flow"]
        assert unbound["binding_status"] == "unbound", unbound
        assert unbound["binding"] is None, unbound
    finally:
        await db.close()


@requires_pg
async def test_decision_referent_binds_on_real_apply_path(tmp_path: Path) -> None:
    """AC-REFM-06: a Decision referent binding survives the fold + /internal read-back."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="d")
        seams = build_real_seams(header, db, referent_corpus=_make_corpus(tmp_path))

        await seams.apply_delta(
            meeting_id,
            _window(0.0, 4.0),
            NoteDelta(
                ops=[
                    AddOp(
                        entry=Decision(
                            text="rewrite the refund path",
                            status=DecisionStatus.final,
                            reversibility=Reversibility.easy,
                            referents=["issue_refund"],
                        )
                    )
                ]
            ),
        )

        notes: Notes = await read_notes(meeting_id, db=db)
        entry = notes.entries[notes.order[0]]
        bindings = _referent_bindings(entry)
        bound = bindings["issue_refund"]
        assert bound["binding_status"] == "bound", bound
        assert bound["binding"] == "payments/refund.py::issue_refund", bound
    finally:
        await db.close()


@requires_pg
async def test_no_corpus_leaves_referents_unbound(tmp_path: Path) -> None:
    """No index configured for the meeting: referents stay honestly unbound (spec-correct)."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="no-corpus")
        seams = build_real_seams(header, db)  # no referent_corpus

        await seams.apply_delta(
            meeting_id,
            _window(0.0, 5.0),
            NoteDelta(
                ops=[
                    AddOp(
                        entry=Claim(
                            text="checkout is slow",
                            speaker="Ana",
                            said_at_s=1.0,
                            firmness=Firmness.firm,
                            provenance=Provenance.observed,
                            referents=["checkout"],
                        )
                    )
                ]
            ),
        )

        notes: Notes = await read_notes(meeting_id, db=db)
        entry = notes.entries[notes.order[0]]
        # The name is kept; with no corpus the binding is honestly unbound.
        assert entry["referents"] == ["checkout"], entry
        bindings = _referent_bindings(entry)
        assert bindings["checkout"]["binding_status"] == "unbound", bindings
        assert bindings["checkout"]["binding"] is None, bindings
    finally:
        await db.close()
