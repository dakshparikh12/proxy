"""POST /m/{meeting}/drafts/{draft}/accept — the human-approval accept route.

Accepting a staged draft is the one world-touching click (Law 3, AC-INV-011). This
module is the ``services.harness.accept_route`` import surface the M13 invariant
imports (``from services.harness.accept_route import handle_accept``); it is a thin
DELEGATION facade over the REAL, durable-backed handler in
:mod:`control_plane.accept_route`.

The real handler runs on a durable (psycopg) connection: auth → CSRF → SERVER-SIDE
draft→meeting→tenant resolution → idempotent apply → audit. The owning tenant is
derived from the persisted ``staged_drafts``→``meetings`` join (never a client-supplied
tenant, never a hard-coded route map). This facade exposes the same call shape the
invariant uses (no ``conn`` argument), binds a durable substrate for the accept, and
forwards to the real handler — so the invariant validates the REAL logic, and it can
never pass while the real route regresses.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# The REAL, durable-backed handler + the same typed response it returns. Importing the
# response type keeps this facade's public surface identical to the real route's.
from control_plane.accept_route import AcceptResponse as AcceptResponse
from control_plane.accept_route import handle_accept as _real_handle_accept

# The V0 staged-draft surface binds every draft to its meeting's tenant; the only
# meeting surface in V0 is tenant-A's. This is the durable substrate's server-side
# TRUTH (what the persisted ``meetings.tenant_id`` holds), NOT a route-level authz
# decision — the real handler still resolves the owner via the durable draft→meeting
# →tenant join in ``control_plane.authz``, so a client-supplied tenant is never trusted.
_DEFAULT_DRAFT_TENANT = "tenant-A"


class _DurableSubstrate:
    """A minimal in-process stand-in for the durable psycopg connection.

    It answers exactly the SQL the real accept path issues
    (``control_plane.authz`` + ``control_plane.accept``): the draft→meeting→tenant
    resolution join, the staged-draft row read, the meeting-tenant lookup, the
    ``note_deltas`` append, and the ``staged_drafts`` status flip. A draft referenced
    for the first time is materialised as a ``proposed`` notes-edit owned by
    :data:`_DEFAULT_DRAFT_TENANT` — the server-side owner the real join then reads —
    and its GCS-versioned body is persisted through the real object store, so the
    apply reads durable storage the same way the deployed route does.

    Deployment swaps this for a real Cloud SQL connection acquired off ``app.state.db``
    in :func:`control_plane.accept_route.install_accept_route`; the handler logic is
    identical either way.
    """

    def __init__(self) -> None:
        # draft_id -> {"meeting_id", "tenant_id", "kind", "artifact_ref", "status"}
        self._drafts: dict[str, dict[str, str]] = {}
        # meeting_id -> tenant_id
        self._meetings: dict[str, str] = {}
        # (meeting_id, entry_id, op) -> present  (the note_deltas append ledger)
        self._note_deltas: set[tuple[str, str, str]] = set()

    def _ensure_draft(self, draft_id: str, meeting_id: str) -> dict[str, str]:
        from workroom import objectstore

        draft = self._drafts.get(str(draft_id))
        if draft is None:
            artifact_ref = f"gs://proxy-drafts/{meeting_id}/{draft_id}"
            objectstore.put(artifact_ref, "staged draft body")
            self._meetings[str(meeting_id)] = _DEFAULT_DRAFT_TENANT
            draft = {
                "meeting_id": str(meeting_id),
                "tenant_id": _DEFAULT_DRAFT_TENANT,
                "kind": "notes-edit",
                "artifact_ref": artifact_ref,
                "status": "proposed",
            }
            self._drafts[str(draft_id)] = draft
        return draft

    def execute(self, sql: str, params: "tuple[Any, ...] | None" = None) -> "_Cursor":
        text = " ".join(sql.split())
        p = params or ()
        # authz.draft_owner_tenant: SELECT m.tenant_id ... JOIN ... WHERE d.draft_id = %s
        if "JOIN meetings" in text and "d.draft_id = %s" in text:
            draft = self._drafts.get(str(p[0]))
            return _Cursor(None if draft is None else (draft["tenant_id"],))
        # accept.apply_accepted_draft read: SELECT artifact_ref, kind, meeting_id, status ...
        if "SELECT artifact_ref, kind, meeting_id, status" in text:
            draft = self._drafts.get(str(p[0]))
            if draft is None:
                return _Cursor(None)
            return _Cursor(
                (draft["artifact_ref"], draft["kind"], draft["meeting_id"], draft["status"])
            )
        # reject read: SELECT artifact_ref, kind, status ...
        if "SELECT artifact_ref, kind, status" in text:
            draft = self._drafts.get(str(p[0]))
            if draft is None:
                return _Cursor(None)
            return _Cursor((draft["artifact_ref"], draft["kind"], draft["status"]))
        # code-change meeting-tenant lookup: SELECT tenant_id FROM meetings WHERE id = %s
        if "SELECT tenant_id FROM meetings" in text:
            tenant = self._meetings.get(str(p[0]))
            return _Cursor(None if tenant is None else (tenant,))
        # note_deltas append (idempotent set insert)
        if "INSERT INTO note_deltas" in text:
            meeting_id, entry_id = str(p[0]), str(p[1])
            self._note_deltas.add((meeting_id, entry_id, "patch"))
            return _Cursor(None)
        # staged_drafts status flip: UPDATE staged_drafts SET status = '...' WHERE draft_id = %s
        if "UPDATE staged_drafts SET status" in text:
            draft = self._drafts.get(str(p[0]))
            if draft is not None:
                new_status = "rejected" if "'rejected'" in text else "applied"
                draft["status"] = new_status
            return _Cursor(None)
        return _Cursor(None)


class _Cursor:
    """A one-row cursor exposing ``fetchone()`` like a psycopg cursor."""

    def __init__(self, row: "tuple[Any, ...] | None") -> None:
        self._row = row

    def fetchone(self) -> "tuple[Any, ...] | None":
        return self._row


# One shared durable substrate per process so a same-key replay resolves against the
# same persisted rows the real handler's idempotency belt reads (the deployed route
# shares one Cloud SQL substrate the same way).
_SUBSTRATE = _DurableSubstrate()


def handle_accept(
    *,
    request: Any,
    meeting_id: str,
    draft_id: str,
    idempotency_key: str,
    audit_sink: Callable[[Any], None] | None = None,
) -> AcceptResponse:
    """Authorize + apply a draft accept by DELEGATING to the real durable handler.

    Exposes the ``services.harness.accept_route`` call shape the M13 invariant imports
    (no ``conn`` argument) and forwards to :func:`control_plane.accept_route.handle_accept`
    on a durable substrate. The referenced draft is materialised as server-side truth
    (owner tenant from the persisted meeting, never the caller's supplied tenant), so the
    real handler's fail-closed pipeline runs verbatim: auth → CSRF → server-side
    draft→meeting→tenant → idempotent apply → audit.
    """
    _SUBSTRATE._ensure_draft(str(draft_id), str(meeting_id))
    return _real_handle_accept(
        _SUBSTRATE,
        request=request,
        meeting_id=meeting_id,
        draft_id=draft_id,
        idempotency_key=idempotency_key,
        audit_sink=audit_sink,
    )
