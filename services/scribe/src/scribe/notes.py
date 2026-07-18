"""Scribe note application — the comprehension flip is atomic with the delta.

``apply_note_delta`` flips transcript_segments.status 'pending'→'comprehended' in
the SAME transaction as the note-delta append. A failure after the flip but
before commit rolls BOTH back, so a segment is never left half-comprehended.
"""
from __future__ import annotations

from typing import Any

from libs.db import Database, repos


async def apply_note_delta(
    db: Database,
    *,
    segment_id: Any,
    delta: str,
    _fail_after_flip: bool = False,
) -> None:
    """Flip comprehension + append the note delta in one transaction."""
    async with db.acquire() as conn:
        async with conn.transaction():
            await repos.transcript.flip_and_append(conn, segment_id, delta)
            if _fail_after_flip:
                # A crash between the flip and the commit must roll both back.
                raise RuntimeError("injected failure after comprehension flip")
