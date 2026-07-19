"""meetings repository — the meeting bound to (tenant, repo, pinned_sha=HEAD).

The Recall bot_id is written back after the bot launches so a webhook can resolve
its bot_id → meeting → (tenant, repo).
"""
from __future__ import annotations

from typing import Any


async def insert_meeting(
    conn: Any,
    *,
    tenant_id: Any,
    repo_id: Any,
    meeting_url: str | None,
    pinned_sha: str | None,
    recall_bot_id: str | None,
    status: str = "live",
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO meetings
            (tenant_id, repo_id, meeting_url, pinned_sha, recall_bot_id, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, tenant_id, repo_id, pinned_sha, recall_bot_id, status
        """,
        tenant_id,
        repo_id,
        meeting_url,
        pinned_sha,
        recall_bot_id,
        status,
    )
    return dict(row)


async def update_bot_id(
    conn: Any, *, meeting_id: Any, recall_bot_id: str
) -> dict[str, Any] | None:
    """Write the launched Recall bot_id back onto the meetings row (AC-JOIN-10).

    Called once the bot actually launches so the stored ``recall_bot_id`` equals
    the launched bot's id — the value a later Recall webhook carries (AC-JOIN-11).
    Returns the updated row, or ``None`` if no meeting matches ``meeting_id``.
    """
    row = await conn.fetchrow(
        """
        UPDATE meetings
           SET recall_bot_id = $2
         WHERE id = $1
        RETURNING id, tenant_id, repo_id, pinned_sha, recall_bot_id, status
        """,
        meeting_id,
        recall_bot_id,
    )
    return dict(row) if row is not None else None


async def get_by_bot_id(conn: Any, recall_bot_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT id, tenant_id, repo_id FROM meetings WHERE recall_bot_id = $1",
        recall_bot_id,
    )
    return dict(row) if row is not None else None
