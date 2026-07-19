"""transcript_segments repository — comprehension flip is atomic with the delta.

A segment starts 'pending' and flips to 'comprehended' in the SAME transaction as
the note-delta append: a rollback leaves it 'pending' (never a half-applied
comprehension).
"""
from __future__ import annotations

from typing import Any


async def flip_and_append(conn: Any, segment_id: Any, delta: str) -> None:
    """Flip status→'comprehended' and append the note delta (one statement).

    Caller wraps this in a transaction; if a later step in that transaction
    fails, both effects roll back together.
    """
    await conn.execute(
        """
        UPDATE transcript_segments
           SET status = 'comprehended',
               note = COALESCE(note, '') || $2
         WHERE id = $1
        """,
        segment_id,
        delta,
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
