"""AC-SCRIBE-ROLLING-WIRED — the rolling summary (Segment B) is LIVE in the real path.

Gap DOC03-ROLLING-SUMMARY-UNWIRED: ``harness.scribe_runtime.build_real_seams`` passed a
HARDCODED empty rolling summary into every ``scribe_call`` (``_real_scribe_call(header,
"", window, ...)``) and NO cadence task ever refreshed it. Per §3.2/§4 the rolling
summary IS cached Segment B — it carries the meeting's history into every micro-call.
With it permanently empty the Scribe saw only the fixed head + the single newest window
and had NO meeting context: it could not resolve back-references, could not detect a
number stated at min 3 vs min 20, could not track a decision's forming->final arc.

These tests drive the REAL production seam (``build_real_seams(header, db)``) against the
REAL Postgres ``note_deltas`` ledger and the REAL canonical fold (``read_notes`` ->
``Notes.fold_all`` -> ``render_for_summary``). No product double is injected: the applier,
the ledger, the fold, the render, the holder swap, the cadence trigger, and the
``scribe_call`` that READS the holder are all production code. The ONLY injected object
is the Segment-B summary-generation LLM client (the one vendor call), exactly as
``scribe.call.scribe_call`` accepts an injected ``client`` — its ``messages.create`` is
handed the rendered live notes and echoes them back, so the assertion is deterministic
while the wiring under test is entirely real.
"""
from __future__ import annotations

import os
import uuid

import pytest

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.notes_reader import read_notes
from scribe.prefix import MeetingHeader, build_scribe_prefix
from scribe.rolling_summary import rolling_summary_every_n_deltas
from scribe.schema import (
    AddOp,
    Claim,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
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


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


class _Outcome:
    def __init__(self, value):
        self.value = value
        self.attempts = 1
        self.total_cost_usd = 0.0


async def _offline_call_external(op, *, service, unit_cost_usd=0.0):
    """The §14 retry+cost funnel substitute for the offline tier — runs the op once,
    wraps in an ExternalCallOutcome-shaped result (the vendor boundary, not the
    product logic under test). Lets the REAL regen/fold/render/swap run without the
    anthropic SDK installed."""
    value = await op()
    return _Outcome(value)


class _EchoSummaryClient:
    """The ONE injected object: the Segment-B summary-generation vendor client.

    Its ``messages.create`` receives the rendered LIVE notes as the user content and
    returns a compact summary derived from it — standing in for the Haiku regen call so
    the wiring assertion is deterministic. Every other symbol in the path is production.
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

        class _Messages:
            async def create(_self, **params):  # noqa: N805
                content = params["messages"][0]["content"]
                self.seen.append(content)
                return _Resp(f"SUMMARY-OF::{content}")

        self.messages = _Messages()


@requires_pg
async def test_refresh_folds_live_notes_and_scribe_call_reads_the_new_segment_b() -> None:
    """refresh_summary folds the real ledger -> the holder scribe_call reads is updated."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(
            meeting_id=meeting_id, agenda="rolling", participants=("Ana", "Zed")
        )
        summary_client = _EchoSummaryClient()
        seams = build_real_seams(header, db, summary_client=summary_client, call_external=_offline_call_external)

        # Segment B starts empty — the Scribe has no meeting context yet.
        assert seams.summary_holder.text == ""

        # A claim lands at "min 3" through the REAL applier + REAL ledger.
        await seams.apply_delta(
            meeting_id,
            _window(180.0, 185.0),
            NoteDelta(
                ops=[
                    AddOp(
                        entry=Claim(
                            text="conversion is 2 percent",
                            speaker="Ana",
                            said_at_s=181.0,
                            firmness=Firmness.hedged,
                            provenance=Provenance.observed,
                        )
                    )
                ],
                current_goal="pin the conversion number",
            ),
        )

        # Drive the rolling-summary refresh on the REAL path: fold live notes -> regen.
        await seams.refresh_summary(meeting_id)

        # The summary model was handed the RENDERED LIVE notes (not the raw transcript).
        assert summary_client.seen, "refresh_summary never invoked the summary generator"
        rendered = summary_client.seen[-1]
        assert "conversion is 2 percent" in rendered, rendered
        assert "GOAL: pin the conversion number" in rendered, rendered

        # Segment B is now LIVE — no longer the hardcoded empty string.
        assert seams.summary_holder.text != ""
        assert "conversion is 2 percent" in seams.summary_holder.text

        # And the very next scribe_call READS it: the built prefix carries Segment B,
        # so the Scribe sees the meeting's history (the number stated earlier).
        prefix = build_scribe_prefix(header, seams.summary_holder.text)
        segment_b_texts = " ".join(
            block.get("text", "") for block in prefix if isinstance(block, dict)
        )
        assert "conversion is 2 percent" in segment_b_texts, prefix
    finally:
        await db.close()


@requires_pg
async def test_summary_captures_decision_forming_to_final_across_windows() -> None:
    """A decision forming->final over windows is carried into Segment B (cross-time)."""
    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="cross-time")
        summary_client = _EchoSummaryClient()
        seams = build_real_seams(header, db, summary_client=summary_client, call_external=_offline_call_external)

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
                    )
                ]
            ),
        )
        decision_id = (await read_notes(meeting_id, db=db)).order[0]
        await seams.apply_delta(
            meeting_id,
            _window(600.0, 604.0),
            NoteDelta(
                ops=[
                    PatchOp(
                        target_id=decision_id,
                        changes={"status": "final"},
                        supersede_reason="ratified 10 minutes later",
                    )
                ]
            ),
        )

        await seams.refresh_summary(meeting_id)
        rendered = summary_client.seen[-1]
        # The rendered live notes carry the FINAL status — the arc, not a window-local view.
        assert "adopt retry backoff" in rendered, rendered
        assert "status=final" in rendered, rendered
        assert seams.summary_holder.text != ""
    finally:
        await db.close()


@requires_pg
async def test_apply_delta_drives_the_cadence_and_fires_a_refresh_off_hot_path() -> None:
    """The REAL apply_delta bumps the cadence and fires a refresh at N deltas (§3.2)."""
    import asyncio

    db = await _db()
    try:
        meeting_id = str(uuid.uuid4())
        header = MeetingHeader(meeting_id=meeting_id, agenda="cadence")
        summary_client = _EchoSummaryClient()
        seams = build_real_seams(header, db, summary_client=summary_client, call_external=_offline_call_external)

        n = rolling_summary_every_n_deltas()
        # Apply N single-op adds through the REAL applier — each bumps the cadence.
        for i in range(n):
            await seams.apply_delta(
                meeting_id,
                _window(float(i), float(i) + 1.0),
                NoteDelta(
                    ops=[
                        AddOp(
                            entry=Claim(
                                text=f"fact number {i}",
                                speaker="Ana",
                                said_at_s=float(i),
                                firmness=Firmness.hedged,
                                provenance=Provenance.observed,
                            )
                        )
                    ]
                ),
            )
        # Let the fire-and-forget refresh task complete (off the hot path).
        await asyncio.sleep(0)
        for _ in range(50):
            if seams.summary_holder.text:
                break
            await asyncio.sleep(0.02)

        assert seams.summary_holder.text != "", "cadence never fired a refresh at N deltas"
        assert summary_client.seen, "the summary generator was never called by the cadence"
        # The cadence reset after firing — the counter is back below N.
        assert seams.summary_state.deltas_since < n
    finally:
        await db.close()
