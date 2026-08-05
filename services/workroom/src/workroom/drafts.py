"""Workroom staged drafts — durable at creation, human-accepted after teardown.

``propose_change`` writes the full body to GCS Object-Versioned storage and
persists a 'proposed' staged_drafts row at creation. ``accept_draft`` reads the
persisted row + object body (never the dead in-memory review session), so a human
can approve long after the sandbox is gone.

**The one write-to-the-world (§3.8).** ``propose_change`` is MULTI-FILE (CANONICAL
§12.9): one call stages a whole code-change draft — ``propose_change(kind, summary,
files:[{path, old_sha?, new_content}] | unified_diff)`` → **ONE** GCS Object-Versioned
bundle + **ONE** ``staged_drafts`` row, returning a ``draft_id`` with
``status=needs_review`` — it NEVER lands and is NEVER pushed (push is Expansion behind
``contents:write``). The GCS/Postgres creds live on the trusted host (CANONICAL §11.7 —
unreachable from the egress-denied, credential-less E2B sandbox), so the write always runs
host-side; the persisted bundle is accepted from durable storage by the accept-handler
AFTER the sandbox is gone (``control_plane.accept``) — never a dead in-memory session.
"""
from __future__ import annotations

import json
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


def _normalize_files(files: Any) -> list[dict[str, Any]]:
    """Normalize the ``files`` argument to a list of ``{path, old_sha?, new_content}``.

    Each file carries the COMPLETE new content per file (§3.8). ``old_sha`` is optional:
    when absent the original for the diff comes from the pinned clone at
    ``meeting.pinned_sha`` (recorded here as the ``original`` source hint; the read is the
    accept-handler / diff-render's job, not this write's — this stage only persists the
    proposed new content + the original pointer, never landing anything).
    """
    normalized: list[dict[str, Any]] = []
    for f in files or []:
        if not isinstance(f, dict) or "path" not in f:
            raise ValueError("each file needs a 'path'")
        entry: dict[str, Any] = {
            "path": f["path"],
            "new_content": f.get("new_content", ""),
        }
        # The original source: the agent's old_sha, else the pinned clone (recorded, not
        # guessed — the diff-render reads it at accept, never fabricated here).
        if "old_sha" in f:
            entry["old_sha"] = f["old_sha"]
        else:
            entry["original_from"] = "meeting.pinned_sha"
        normalized.append(entry)
    return normalized


def _build_bundle(
    *, kind: str, files: Any = None, unified_diff: str | None = None, content: str | bytes | None = None
) -> str:
    """Build the ONE bundle body persisted to GCS at creation (a single JSON blob).

    Accepts EITHER a multi-file ``files`` list OR a ``unified_diff`` (§3.8 / CANONICAL
    §12.9); the legacy single ``content`` rides the same bundle as a one-file list so a
    core notes-edit accept still reads a plain string. Exactly one bundle is produced —
    all files / the diff live in this single Object-Versioned blob (never one blob per
    file).
    """
    if content is not None and files is None and unified_diff is None:
        # Legacy single-content path (e.g. a notes-edit): the body IS the plain string so
        # the notes-edit accept path reads it verbatim (no JSON envelope to unwrap).
        return content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    if not files and not unified_diff:
        raise ValueError("propose_change needs a 'files' list or a 'unified_diff'")
    bundle = {
        "kind": kind,
        "files": _normalize_files(files),
        "unified_diff": unified_diff,
    }
    return json.dumps(bundle)


def _persist_bundle_row_sync(
    conn: Any, *, meeting_id: Any, kind: str, summary: str, body: str
) -> ProposedDraft:
    """Persist ONE GCS bundle + ONE staged_drafts row (sync psycopg path) at creation.

    Durable BEFORE teardown (CANONICAL §4): the body is written to Object-Versioned
    storage and a SINGLE 'proposed' row is inserted, then the ``draft_id`` is returned.
    Exactly one object + one row — never one row/blob per file.
    """
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
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
        status="needs_review",
        review_session_id=uuid.uuid4().hex,
    )


async def _propose_change_async(
    db: Database,
    *,
    meeting_id: Any,
    kind: str,
    summary: str,
    body: str,
) -> ProposedDraft:
    """Persist a draft at creation (async pool path): ONE body → store, ONE row → DB."""
    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, body)
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
        status="needs_review",
        review_session_id=uuid.uuid4().hex,
    )


def propose_change(
    db: Any = None,
    *,
    meeting_id: Any,
    kind: str = "code-change",
    summary: str,
    files: Any = None,
    unified_diff: str | None = None,
    content: str | bytes | None = None,
) -> Any:
    """Stage a MULTI-FILE change draft, durable at creation (§3.8 / CANONICAL §12.9).

    Accepts EITHER a multi-file ``files:[{path, old_sha?, new_content}]`` list OR a
    ``unified_diff`` — one call stages a whole code-change draft. The legacy single
    ``content`` keyword still works for a notes-edit. It persists **exactly one** GCS
    Object-Versioned bundle (all files / the diff in one blob) + **exactly one**
    ``staged_drafts`` row the moment it is proposed (so it survives the Workroom sandbox
    teardown), and returns a ``draft_id`` with ``status=needs_review``. It NEVER lands and
    is NEVER pushed (propose-not-apply, §3.8).

    ``Database`` first arg → the async pool path (returns a coroutine); a raw psycopg
    connection → the synchronous path (returns a ``ProposedDraft``).
    """
    body = _build_bundle(kind=kind, files=files, unified_diff=unified_diff, content=content)
    if isinstance(db, Database):
        return _propose_change_async(db, meeting_id=meeting_id, kind=kind, summary=summary, body=body)
    return _persist_bundle_row_sync(db, meeting_id=meeting_id, kind=kind, summary=summary, body=body)


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
