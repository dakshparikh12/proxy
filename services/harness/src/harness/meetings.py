"""Invite + bot-id resolution — a meeting bound to (tenant, repo, pinned_sha).

Inviting Proxy creates a meetings row bound to (tenant, repo, pinned_sha=HEAD)
and, once the Recall bot launches, binds the bot_id back onto the row. A webhook
resolves its bot_id → meeting → (tenant, repo).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos


@dataclass(frozen=True)
class InvitedMeeting:
    id: Any
    tenant_id: Any
    repo_id: Any
    pinned_sha: Any
    recall_bot_id: str


async def invite_proxy(
    db: Database,
    *,
    tenant_id: Any,
    repo_id: Any,
    meeting_url: str,
    head_sha: str,
) -> InvitedMeeting:
    """Create the meeting bound to (tenant, repo, pinned_sha=HEAD) + bind bot_id."""
    recall_bot_id = f"recall-bot-{uuid.uuid4().hex}"  # written back after launch
    async with db.acquire() as conn:
        row = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant_id,
            repo_id=repo_id,
            meeting_url=meeting_url,
            pinned_sha=head_sha,
            recall_bot_id=recall_bot_id,
            status="live",
        )
    return InvitedMeeting(
        id=row["id"],
        tenant_id=row["tenant_id"],
        repo_id=row["repo_id"],
        pinned_sha=row["pinned_sha"],
        recall_bot_id=recall_bot_id,
    )


async def resolve_bot_id(db: Database, recall_bot_id: str) -> dict[str, Any] | None:
    """Resolve a Recall bot_id back to its meeting (→ tenant + repo)."""
    async with db.acquire() as conn:
        row = await repos.meetings.get_by_bot_id(conn, recall_bot_id)
    if row is None:
        return None
    return {"meeting_id": row["id"], "tenant_id": row["tenant_id"], "repo_id": row["repo_id"]}
