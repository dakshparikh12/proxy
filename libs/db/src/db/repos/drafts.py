"""staged_drafts repository — a draft is durable the moment it is proposed.

The full body lives in GCS (Object-Versioned); the row carries the pointer
(``artifact_ref``) plus review metadata so a human can accept it long after the
Workroom sandbox is torn down.
"""
from __future__ import annotations

from typing import Any


async def insert_draft(
    conn: Any,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    artifact_ref: str,
    status: str = "proposed",
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING draft_id, meeting_id, kind, summary, artifact_ref, status, created_at
        """,
        meeting_id,
        kind,
        summary,
        artifact_ref,
        status,
    )
    return dict(row)


async def get_draft(conn: Any, draft_id: Any) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT draft_id, meeting_id, kind, summary, artifact_ref, status, created_at
          FROM staged_drafts
         WHERE draft_id = $1
        """,
        draft_id,
    )
    return dict(row) if row is not None else None


async def set_draft_status(conn: Any, draft_id: Any, status: str) -> None:
    await conn.execute(
        "UPDATE staged_drafts SET status = $2 WHERE draft_id = $1",
        draft_id,
        status,
    )


async def list_drafts_for_meeting(conn: Any, meeting_id: Any) -> list[dict[str, Any]]:
    """Every staged draft for ONE meeting, oldest-first — the §2.8 home's cards.

    Scoped to a single ``meeting_id`` on purpose: the ``/m/{meeting_id}`` home is
    **one meeting's** notes + drafts, never a cross-meeting list (§2.8 — "not a
    dashboard"). The caller has already proven ``meeting→tenant`` server-side, so
    this reader is a pure per-meeting projection; it never widens beyond the one id.
    """
    rows = await conn.fetch(
        """
        SELECT draft_id, meeting_id, kind, summary, artifact_ref, status, created_at
          FROM staged_drafts
         WHERE meeting_id = $1
         ORDER BY created_at ASC, draft_id ASC
        """,
        meeting_id,
    )
    return [dict(r) for r in rows]
