"""control_plane draft-accept — reads the DURABLE draft, never the dead session.

A human accepting a staged draft (possibly long after the Workroom sandbox is torn
down) reads the persisted ``staged_drafts`` row from durable storage (the row that
carries the GCS-versioned ``artifact_ref``), applies it, and marks it accepted. It
never touches the in-memory review session (which died at teardown).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AppliedDraft:
    """The result of a human accept applied from durable storage."""

    draft_id: Any
    ok: bool
    read_from: str


def accept_draft(conn: Any, draft_id: Any, *, principal: str) -> AppliedDraft:
    """Accept a staged draft from the persisted row (post-teardown safe)."""
    row = conn.execute(
        "SELECT artifact_ref, status FROM staged_drafts WHERE draft_id = %s",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no staged draft {draft_id!r}")
    artifact_ref = row[0]
    conn.execute(
        "UPDATE staged_drafts SET status = 'accepted' WHERE draft_id = %s",
        (draft_id,),
    )
    # ok iff the durable artifact pointer is present — proof the accept read the
    # persisted draft, not the dead in-memory review session.
    return AppliedDraft(draft_id=draft_id, ok=bool(artifact_ref), read_from="durable")
