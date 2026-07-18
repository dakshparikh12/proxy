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
