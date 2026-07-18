"""Workroom staged drafts — durable at creation, human-accepted after teardown.

``propose_change`` writes the full body to GCS Object-Versioned storage and
persists a 'proposed' staged_drafts row at creation. ``accept_draft`` reads the
persisted row + object body (never the dead in-memory review session), so a human
can approve long after the sandbox is gone.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos

from . import objectstore


@dataclass(frozen=True)
class ProposedDraft:
    draft_id: Any
    meeting_id: Any
    artifact_ref: str
    status: str


@dataclass(frozen=True)
class AcceptedDraft:
    draft_id: Any
    content: str
    applied: bool
    read_from: str


async def propose_change(
    db: Database,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    content: str,
) -> ProposedDraft:
    """Persist a draft at creation: body → object store, 'proposed' row → DB."""
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, content)
    async with db.acquire() as conn:
        row = await repos.drafts.insert_draft(
            conn,
            meeting_id=meeting_id,
            kind=kind,
            summary=summary,
            artifact_ref=artifact_ref,
            status="proposed",
        )
    return ProposedDraft(
        draft_id=row["draft_id"],
        meeting_id=row["meeting_id"],
        artifact_ref=row["artifact_ref"],
        status=row["status"],
    )


async def accept_draft(
    db: Database, draft_id: Any, *, review_session: Any = None
) -> AcceptedDraft:
    """Accept from durable storage — reads the persisted row + object body."""
    async with db.acquire() as conn:
        row = await repos.drafts.get_draft(conn, draft_id)
        if row is None:
            raise LookupError(f"no staged draft {draft_id!r}")
        content = objectstore.get(row["artifact_ref"]) or ""
        await repos.drafts.set_draft_status(conn, draft_id, "accepted")
    return AcceptedDraft(
        draft_id=draft_id,
        content=content,
        applied=bool(content),
        read_from="durable",
    )
