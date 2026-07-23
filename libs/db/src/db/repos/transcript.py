"""transcript_segments repository — comprehension flip is atomic with the delta.

A segment starts 'pending' and flips to 'comprehended' in the SAME transaction as
the note-delta append: a rollback leaves it 'pending' (never a half-applied
comprehension).
"""
from __future__ import annotations

import json
from typing import Any


async def flip_and_append(conn: Any, segment_id: Any, delta: str) -> None:
    """Flip status→'comprehended' AND append the note delta to ``note_deltas`` — one tx.

    Per §3.3/§3.1 the comprehension flip is transactional with the **note_deltas
    append**, NOT a write to ``transcript_segments``: that table has no ``note``
    column (the early 0001 ``note`` column was dropped when migration 0004
    reconciled the table to the sealed §3.3 schema). The caller wraps this in a
    transaction; a failure rolls BOTH the append and the flip back together, so a
    segment is never left half-comprehended.

    ``entry_id``/``op`` are this seam's minimal faithful values for a fresh add
    (a segment-keyed 'add'); the rich fold path (``scribe.pipeline``) supplies the
    real entry identity via ``repos.notes.append_delta`` directly.
    """
    meeting_id = await conn.fetchval(
        "SELECT meeting_id FROM transcript_segments WHERE id = $1", segment_id
    )
    # Append the delta to the append-only ledger (§3.3); ON CONFLICT DO NOTHING
    # keeps a stray re-append a silent no-op (replay idempotency, §3.3).
    await conn.execute(
        """
        INSERT INTO note_deltas (meeting_id, entry_id, op, payload, window_start_s)
        VALUES ($1, $2, 'add', $3::jsonb, NULL)
        ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING
        """,
        meeting_id,
        f"seg-{segment_id}",
        json.dumps({"delta": delta}),
    )
    # Flip comprehension in the SAME transaction (§3.1 coupling).
    await conn.execute(
        "UPDATE transcript_segments SET status = 'comprehended' WHERE id = $1",
        segment_id,
    )


async def pending_segment_ids(conn: Any, meeting_id: Any) -> list[str]:
    """Every still-``pending`` segment id for ONE meeting — the close reconciler's read.

    Meeting-scoped by construction (tenant isolation, invariant 9): only this meeting's
    un-transcribed segments are candidates for the mark-lost backfill at close — never a
    cross-meeting sweep. Returned in stable creation order so the backfill is deterministic.
    This is the third of the three tenant-safe primitives the close reconciler's scoped
    segment-store adapter drives (with :func:`flip_and_append` and
    :func:`backfill_segment_as_lost`).
    """
    rows = await conn.fetch(
        """
        SELECT id
          FROM transcript_segments
         WHERE meeting_id = $1
           AND status = 'pending'
         ORDER BY created_at
        """,
        meeting_id,
    )
    return [str(row["id"]) for row in rows]


async def backfill_segment_as_lost(conn: Any, segment_id: Any) -> None:
    """Mark a still-``pending`` segment as ``lost`` at meeting close (AC-FAIL-10, §3.7).

    The honest gap path: any segment never transcribed (still ``pending`` when the
    meeting closes) is recorded ``lost`` — never silently dropped, never faked as
    comprehended. The ``AND status = 'pending'`` guard makes this idempotent and
    keeps it from ever overwriting a segment that was already comprehended, so a
    re-run of the close reconciler is a no-op.
    """
    await conn.execute(
        """
        UPDATE transcript_segments
           SET status = 'lost'
         WHERE id = $1
           AND status = 'pending'
        """,
        segment_id,
    )
