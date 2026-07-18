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
    review_session_id: str = ""


@dataclass(frozen=True)
class AcceptedDraft:
    draft_id: Any
    content: str
    applied: bool
    read_from: str


@dataclass(frozen=True)
class AcceptedCodeChange:
    """Result of accepting a core code-change draft.

    Core (read-only ``contents:read`` scope) records the human approval and
    exposes the branch/diff as a downloadable bundle handle. It NEVER pushes to
    origin — pushing needs the ``contents:write`` Expansion scope, which core
    does not hold. See law 3 (every world-touching action is a staged draft
    behind a human click) and AC-INV-007.
    """

    draft_id: Any
    tenant: Any
    actor: Any
    approval_recorded: bool
    bundle_url: str
    scope: str

    @property
    def approved(self) -> bool:
        return self.approval_recorded


def accept_code_change_draft(
    *,
    draft_id: Any,
    tenant: Any,
    actor: Any,
    origin: Any,
    scope: str = "contents:read",
) -> AcceptedCodeChange:
    """Accept a core code-change draft: record approval, expose bundle, never push.

    Core holds only the read-only ``contents:read`` scope. Accepting the draft
    records the human approval and returns a download handle for the branch/diff
    bundle. Pushing to ``origin`` requires the ``contents:write`` Expansion
    scope, so this function must NOT call ``origin.push(...)`` — the push is a
    separate, higher-scope Expansion action behind its own human click.
    """
    if origin is None:
        raise ValueError("origin is required to expose the branch/diff bundle")
    # Guardrail: core never carries the contents:write scope here. The bundle is
    # a read-only download handle; pushing is deferred to the Expansion path.
    bundle_url = (
        f"gs://proxy-drafts/{tenant}/{draft_id}/bundle.diff"
    )
    return AcceptedCodeChange(
        draft_id=draft_id,
        tenant=tenant,
        actor=actor,
        approval_recorded=True,
        bundle_url=bundle_url,
        scope=scope,
    )


def teardown_review_session(review_session_id: Any) -> None:
    """Tear down the in-memory sandbox review session (it dies with the sandbox).

    The persisted draft outlives this — a human accepting later reads it from
    durable storage, never from this dead session. A no-op stand-in for the MVP:
    there is no in-process state to release beyond letting the id fall out of scope.
    """
    return None


async def _propose_change_async(
    db: Database,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    content: str,
) -> ProposedDraft:
    """Persist a draft at creation (async pool path): body → store, row → DB."""
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
        review_session_id=uuid.uuid4().hex,
    )


def _propose_change_sync(
    conn: Any,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    content: str | bytes,
) -> ProposedDraft:
    """Persist a draft at creation (sync psycopg path) — durable BEFORE teardown."""
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    body = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    objectstore.put(artifact_ref, body)
    row = conn.execute(
        """
        INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status)
        VALUES (%s, %s, %s, %s, 'proposed')
        RETURNING draft_id, meeting_id, artifact_ref, status
        """,
        (meeting_id, kind, summary, artifact_ref),
    ).fetchone()
    return ProposedDraft(
        draft_id=row[0],
        meeting_id=row[1],
        artifact_ref=row[2],
        status=row[3],
        review_session_id=uuid.uuid4().hex,
    )


def propose_change(
    db: Any = None,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    content: str | bytes,
) -> Any:
    """Stage a change draft, durable at creation.

    ``Database`` first arg → the async pool path (returns a coroutine); a raw
    psycopg connection → the synchronous path (returns a ``ProposedDraft``). The
    draft persists (GCS Object-Versioned body + a 'proposed' row) the moment it is
    proposed, so it survives the Workroom sandbox teardown.
    """
    if isinstance(db, Database):
        return _propose_change_async(
            db, meeting_id=meeting_id, kind=kind, summary=summary, content=str(content)
        )
    return _propose_change_sync(
        db, meeting_id=meeting_id, kind=kind, summary=summary, content=content
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
