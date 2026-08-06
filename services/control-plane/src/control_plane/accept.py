"""control_plane draft-accept — reads the DURABLE draft, never the dead session.

A human accepting a staged draft (possibly long after the Workroom sandbox is torn
down) reads the persisted ``staged_drafts`` row from durable storage (the row that
carries the GCS-versioned ``artifact_ref``), applies it, and marks it applied. It
never touches the in-memory review session (which died at teardown).

The apply is kind-aware (§3.16.1 / CANONICAL §4/§12.9):

  * a ``code-change`` draft       → RECORD the approval (flip to ``applied``) + expose
                                    the already-persisted diff bundle for download.
                                    It NEVER pushes / opens a PR — push is an Expansion
                                    seam behind the ``contents:write`` scope the core
                                    deliberately does not hold (Law 3, AC-INV-007).
  * any other kind (a legacy      → RECORD the approval (flip to ``applied``) only. The
    ``notes-edit`` draft)           §2.6 notes fold that once appended to ``note_deltas``
                                    on accept was removed in the workroom pivot, so a
                                    notes-edit accept is now a status-flip with no
                                    durable write.

This module deals in a SYNCHRONOUS psycopg connection (the post-teardown accept path):
the accept can arrive long after the meeting's async harness is gone, so it runs on a
plain durable connection, not the live meeting runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Draft kinds that get the code-change (no-push) apply — a download bundle is exposed. Every
# other kind (e.g. a legacy ``notes-edit``) is a plain approval status-flip: the §2.6 notes
# fold that once read ``note_deltas`` was removed in the workroom pivot, so there is no durable
# note write on accept anymore.
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


def apply_accepted_draft(conn: Any, *, meeting_id: Any, draft_id: Any) -> AppliedDraft:
    """Apply a staged draft from its persisted row (post-teardown safe, kind-aware).

    Reads the DURABLE ``staged_drafts`` row, records approval (exposing the diff
    bundle for a code-change, never pushing), and flips the row to
    ``status='applied'``. Raises :class:`LookupError` when the draft does not exist.
    Never reads the dead in-memory review session — proof is ``read_from == 'durable'``.
    """
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

    # Record approval and flip the row to 'applied'. A code-change additionally exposes its
    # download bundle (computed above) but NEVER pushes (Expansion seam, §12.9); any other kind
    # (a legacy notes-edit) is a status-flip only — the §2.6 notes fold that once wrote
    # ``note_deltas`` on accept was removed in the workroom pivot, so there is no durable write.
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


def reject_staged_draft(conn: Any, *, meeting_id: Any, draft_id: Any) -> AppliedDraft:
    """Decline a staged draft from its persisted row (post-teardown safe, no-apply).

    Reject is the symmetric twin of :func:`apply_accepted_draft` (§3.16.1, CANONICAL
    §12.9): it reads the DURABLE ``staged_drafts`` row and flips it to
    ``status='rejected'`` — but it applies NOTHING (no durable write for a notes-edit;
    no push for a code-change: reject is the opposite of a push). It is kind-aware only
    insofar as a code-change reject still never touches the push seam.

    The DURABLE idempotency belt is the same row-status witness the accept uses: an
    already-terminal row (``status IN ('applied','rejected')``) is NOT re-written, so a
    replayed reject is a no-op AND a reject can never un-apply a draft the human already
    accepted (the ``applied`` state wins — a later reject reports it, never flips it).

    Raises :class:`LookupError` when the draft does not exist. Never reads the dead
    in-memory review session — proof is ``read_from == 'durable'``.
    """
    row = conn.execute(
        "SELECT artifact_ref, kind, status FROM staged_drafts WHERE draft_id = %s",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no staged draft {draft_id!r}")
    artifact_ref, kind, row_status = row[0], (row[1] or ""), row[2]

    # DURABLE idempotency/terminal belt: an already-applied OR already-rejected draft
    # is NOT re-written. A reject NEVER un-applies an applied draft — the terminal row
    # status is the durable witness (holds even without the route's in-memory ledger).
    if row_status in ("applied", "rejected"):
        return AppliedDraft(
            draft_id=draft_id,
            ok=bool(artifact_ref),
            read_from="durable",
            kind=kind,
            applied_status=row_status,
            bundle_url=None,
            pushed=False,
            already_applied=True,
        )

    conn.execute(
        "UPDATE staged_drafts SET status = 'rejected' WHERE draft_id = %s",
        (draft_id,),
    )
    return AppliedDraft(
        draft_id=draft_id,
        ok=bool(artifact_ref),
        read_from="durable",
        kind=kind,
        applied_status="rejected",
        bundle_url=None,  # a declined draft exposes no bundle
        pushed=False,  # reject NEVER pushes
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
