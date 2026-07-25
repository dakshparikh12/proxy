"""control_plane draft-accept — reads the DURABLE draft, never the dead session.

A human accepting a staged draft (possibly long after the Workroom sandbox is torn
down) reads the persisted ``staged_drafts`` row from durable storage (the row that
carries the GCS-versioned ``artifact_ref``), applies it, and marks it applied. It
never touches the in-memory review session (which died at teardown).

The apply is kind-aware (§3.16.1 / CANONICAL §4/§12.9):

  * a core ``notes-edit`` draft  → write the edit into the notes object via Doc 03's
                                    durable write path (a ``note_deltas`` append),
                                    then flip the row to ``status='applied'``;
  * a ``code-change`` draft       → RECORD the approval (flip to ``applied``) + expose
                                    the already-persisted diff bundle for download.
                                    It NEVER pushes / opens a PR — push is an Expansion
                                    seam behind the ``contents:write`` scope the core
                                    deliberately does not hold (Law 3, AC-INV-007).

This module deals in a SYNCHRONOUS psycopg connection (the post-teardown accept path):
the accept can arrive long after the meeting's async harness is gone, so it runs on a
plain durable connection, not the live meeting runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Draft kinds that are a core notes-edit apply vs a code-change (no-push) apply.
_NOTES_EDIT_KINDS = frozenset({"notes-edit"})
_CODE_CHANGE_KINDS = frozenset({"code-change", "file-change"})


@dataclass(frozen=True)
class AppliedDraft:
    """The result of a human accept applied from durable storage."""

    draft_id: Any
    ok: bool
    read_from: str
    kind: str
    applied_status: str
    bundle_url: str | None = None
    pushed: bool = False
    already_applied: bool = False


def _bundle_url(tenant_id: Any, draft_id: Any) -> str:
    """The read-only download handle for a code-change draft's diff bundle.

    A stable GCS URI to the already-persisted branch/diff bundle. It is a download
    handle only — exposing it records nothing that pushes; the ``contents:write``
    push is a separate, higher-scope Expansion action behind its own human click.
    """
    return f"gs://proxy-drafts/{tenant_id}/{draft_id}/bundle.diff"


def _apply_notes_edit(conn: Any, *, meeting_id: Any, draft_id: Any, content: str) -> None:
    """Write the notes edit into the durable notes object (Doc 03's write path).

    The notes object is the deterministic left-fold of the append-only
    ``note_deltas`` ledger (§3.3); applying a notes-edit is a ``patch`` append
    keyed by the draft id so a replay is a silent no-op on the ledger's UNIQUE
    INDEX (belt-and-suspenders behind the route's own idempotency ledger). The
    payload carries the accepted edit body verbatim.
    """
    payload = json.dumps({"text": content, "source": "accept-handler"})
    conn.execute(
        """
        INSERT INTO note_deltas (meeting_id, entry_id, op, payload, window_start_s)
        VALUES (%s, %s, 'patch', %s::jsonb, NULL)
        ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING
        """,
        (meeting_id, f"draft:{draft_id}", payload),
    )


def apply_accepted_draft(conn: Any, *, meeting_id: Any, draft_id: Any) -> AppliedDraft:
    """Apply a staged draft from its persisted row (post-teardown safe, kind-aware).

    Reads the DURABLE ``staged_drafts`` row + its GCS-versioned body, applies the
    edit for a core notes-edit (Doc 03 write path), records approval for a
    code-change (never pushing), and flips the row to ``status='applied'``. Raises
    :class:`LookupError` when the draft does not exist. Never reads the dead
    in-memory review session — proof is ``read_from == 'durable'``.
    """
    from workroom import objectstore

    row = conn.execute(
        "SELECT artifact_ref, kind, meeting_id, status FROM staged_drafts WHERE draft_id = %s",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no staged draft {draft_id!r}")
    artifact_ref, kind, row_meeting_id, row_status = row[0], (row[1] or ""), row[2], row[3]

    bundle_url: str | None = None
    if kind in _CODE_CHANGE_KINDS:
        tenant_row = conn.execute(
            "SELECT tenant_id FROM meetings WHERE id = %s", (row_meeting_id,)
        ).fetchone()
        tenant_id = tenant_row[0] if tenant_row is not None else "unknown"
        bundle_url = _bundle_url(tenant_id, draft_id)

    # DURABLE idempotency belt (post-restart safe): an already-applied/rejected draft
    # is NOT re-applied (§3.16.1). This holds even without the route's in-memory ledger
    # (e.g. a replay after a process recycle) — the row status is the durable witness.
    if row_status in ("applied", "rejected"):
        return AppliedDraft(
            draft_id=draft_id,
            ok=bool(artifact_ref),
            read_from="durable",
            kind=kind,
            applied_status=row_status,
            bundle_url=bundle_url,
            pushed=False,
            already_applied=True,
        )

    # Read the body from DURABLE object storage (the GCS-versioned artifact), never
    # a dead in-memory session.
    content = objectstore.get(artifact_ref) or ""

    if kind in _CODE_CHANGE_KINDS:
        # Record approval + expose the bundle (computed above); NEVER push
        # (Expansion seam, §12.9). No notes-edit is written for a code-change.
        pass
    else:
        # Core notes-edit: write the edit into the notes object durably.
        _apply_notes_edit(conn, meeting_id=row_meeting_id, draft_id=draft_id, content=content)

    conn.execute(
        "UPDATE staged_drafts SET status = 'applied' WHERE draft_id = %s",
        (draft_id,),
    )
    return AppliedDraft(
        draft_id=draft_id,
        ok=bool(artifact_ref),
        read_from="durable",
        kind=kind,
        applied_status="applied",
        bundle_url=bundle_url,
        pushed=False,  # core NEVER pushes — the push is an Expansion seam
    )


def accept_draft(conn: Any, draft_id: Any, *, principal: str) -> AppliedDraft:
    """Backwards-compatible entrypoint — accept a staged draft from the persisted row.

    Retained for callers that hold only ``(conn, draft_id)``; delegates to the
    kind-aware :func:`apply_accepted_draft` (the ``meeting_id`` is resolved off the
    persisted row inside the apply). The ``principal`` is accepted for audit call
    sites but the tenant barrier lives in :mod:`control_plane.authz`.
    """
    _ = principal  # the acting principal is audited by the route, not re-checked here
    return apply_accepted_draft(conn, meeting_id=None, draft_id=draft_id)
