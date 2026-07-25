"""POST /m/{meeting_id}/drafts/{draft_id}/accept — the human-approval accept route.

Accepting a staged draft is the one world-touching click (Law 3), so the route is
hardened (§3.16.1, CANONICAL §12.9): an unauthenticated caller is rejected; an
invalid CSRF token is rejected; a member of a DIFFERENT tenant is rejected by a
SERVER-SIDE draft->meeting->tenant check (a client-supplied tenant is NEVER trusted);
a correct tenant member succeeds and the SAME idempotency key replays the first result
instead of double-applying; and every accept is audited with the acting tenant member.

The apply reads DURABLE storage (the persisted ``staged_drafts`` row + its GCS body),
never the dead in-memory review session (post-teardown safe). It is kind-aware: a core
``notes-edit`` writes the edit into the notes object and flips the row to ``applied``; a
``code-change`` records approval + exposes the diff bundle for download and NEVER pushes
(push is an Expansion seam behind the ``contents:write`` scope the core does not hold).

Two surfaces live here: :func:`handle_accept` is the framework-agnostic handler that runs
on a durable psycopg connection (unit/integration-testable post-teardown), and
:func:`install_accept_route` mounts it as the authenticated ``control_plane`` route
against ``app.state.db``.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import accept as _accept
from . import authz as _authz

if TYPE_CHECKING:
    from fastapi import FastAPI

# Idempotency ledger: (meeting, draft, key) -> the first apply's AcceptResponse fields.
# The same key replays the FIRST result and never re-runs the apply (§3.16.1, §12.9).
_ACCEPTS: dict[tuple[str, str, str], "AcceptResponse"] = {}


@dataclass(frozen=True)
class AcceptResponse:
    """The accept route's typed response."""

    status: int
    accepted: bool = False
    rejected: bool = False
    accept_id: str | None = None
    idempotent_replay: bool = False
    kind: str | None = None
    applied_status: str | None = None
    bundle_url: str | None = None
    pushed: bool = False


def _authenticated(request: Any) -> bool:
    """A request is authenticated iff the auth wall attached a user/session."""
    return bool(getattr(request, "authenticated", getattr(request, "user", None) is not None))


def handle_accept(
    conn: Any,
    *,
    request: Any,
    meeting_id: str,
    draft_id: str,
    idempotency_key: str,
    audit_sink: Callable[[Any], None] | None = None,
) -> AcceptResponse:
    """Authorize + apply a draft accept on DURABLE storage (idempotent, audited).

    ``conn`` is a durable (sync psycopg) connection — the accept can arrive long
    after the meeting harness is gone, so the apply runs on durable storage, never
    the dead in-memory review session. Order is fail-closed: auth -> CSRF ->
    server-side tenant -> (replay? return first) -> apply -> audit.
    """
    # (1) Authentication: an unauthenticated caller cannot accept.
    if not _authenticated(request):
        return AcceptResponse(status=401, rejected=True)

    # (2+3) CSRF + SERVER-SIDE draft->meeting->tenant barrier. The owning tenant is
    #       derived from the persisted row, never from a client-supplied tenant.
    try:
        _authz.authorize_draft_accept(
            conn,
            draft_id=draft_id,
            principal_tenant=getattr(request, "tenant", None),
            csrf_valid=getattr(request, "csrf_valid", True),
        )
    except _authz.CsrfInvalid:
        return AcceptResponse(status=403, rejected=True)
    except (_authz.CrossTenantReadDenied, LookupError):
        # A different tenant OR an unknown draft — both are refused, and NOTHING is
        # applied (the barrier is strictly upstream of the world-touching apply).
        return AcceptResponse(status=403, rejected=True)

    # (4) Idempotency: the SAME key replays the first result, never double-applies.
    key = (str(meeting_id), str(draft_id), str(idempotency_key))
    prior = _ACCEPTS.get(key)
    if prior is not None:
        return AcceptResponse(
            status=prior.status,
            accepted=prior.accepted,
            accept_id=prior.accept_id,
            idempotent_replay=True,
            kind=prior.kind,
            applied_status=prior.applied_status,
            bundle_url=prior.bundle_url,
            pushed=prior.pushed,
        )

    # (5) Apply from DURABLE storage (kind-aware: notes-edit apply vs code-change
    #     record+expose, never push).
    try:
        applied = _accept.apply_accepted_draft(
            conn, meeting_id=meeting_id, draft_id=draft_id
        )
    except LookupError:
        return AcceptResponse(status=404, rejected=True)

    accept_id = uuid.uuid4().hex
    response = AcceptResponse(
        status=200,
        accepted=True,
        accept_id=accept_id,
        idempotent_replay=False,
        kind=applied.kind,
        applied_status=applied.applied_status,
        bundle_url=applied.bundle_url,
        pushed=applied.pushed,
    )
    _ACCEPTS[key] = response

    # (6) Audit: capture the accepting tenant member (never a secret).
    if audit_sink is not None:
        audit_sink(
            f"accept meeting={meeting_id} draft={draft_id} "
            f"tenant={getattr(request, 'tenant', None)} "
            f"user={getattr(request, 'user', None)} "
            f"kind={applied.kind} accept_id={accept_id}"
        )
    return response


# ── The authenticated control_plane route mount ──────────────────────────────
ACCEPT_PATH = "/m/{meeting_id}/drafts/{draft_id}/accept"


class _AuthzedRequest:
    """Adapts a Starlette request into the principal shape :func:`handle_accept` reads.

    The signed-session middleware exposes the logged-in user on ``request.session``;
    the tenant rides the session (server-side), never a client-supplied field. A
    missing/invalid CSRF header fails the CSRF gate.
    """

    def __init__(self, *, authenticated: bool, tenant: Any, user: Any, csrf_valid: bool) -> None:
        self.authenticated = authenticated
        self.tenant = tenant
        self.user = user
        self.csrf_valid = csrf_valid


def install_accept_route(app: "FastAPI") -> None:
    """Mount POST /m/{meeting_id}/drafts/{draft_id}/accept BEHIND the auth wall.

    The route resolves the durable connection off ``app.state.db`` (a missing handle
    is an honest 503, never a fabricated 200), derives the principal + tenant from the
    signed session server-side, checks the CSRF header, and delegates to
    :func:`handle_accept`. The response status is the handler's status verbatim.

    ``app`` is the concrete :class:`fastapi.FastAPI` ``create_app`` builds; the
    annotation gives ``app.post`` a typed signature so the route decorator is a
    typed decorator under ``mypy --strict`` (an ``app: Any`` would make the mounted
    handler untyped — ``[untyped-decorator]``).
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    @app.post(ACCEPT_PATH, include_in_schema=True)
    async def accept_draft_route(meeting_id: str, draft_id: str, request: Request) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return Response(status_code=503)  # no durable substrate handle -> honest 503

        try:
            session = request.session.get("user")
        except (AssertionError, AttributeError):
            session = None  # no SessionMiddleware installed -> treated as no session
        if not session:
            return Response(status_code=401)

        # Tenant + CSRF are derived SERVER-SIDE from the session/headers, never a
        # client-supplied body field.
        tenant = session.get("tenant_id") if isinstance(session, dict) else None
        csrf_header = request.headers.get("X-CSRF-Token")
        csrf_cookie = request.cookies.get("csrf_token")
        csrf_valid = bool(csrf_header) and csrf_header == csrf_cookie
        idem_key = request.headers.get("Idempotency-Key", "")

        principal = _AuthzedRequest(
            authenticated=True,
            tenant=tenant,
            user=session.get("email") if isinstance(session, dict) else session,
            csrf_valid=csrf_valid,
        )

        # The durable connection is acquired from the pool for this one apply.
        async with db.acquire() as aconn:  # noqa: F841 - async pool handle
            # The kind-aware apply is synchronous psycopg SQL; run it on a durable
            # connection borrowed from the same substrate. In deployment the pool
            # yields a psycopg-shaped conn; the handler owns the transaction.
            resp = handle_accept(
                aconn,
                request=principal,
                meeting_id=meeting_id,
                draft_id=draft_id,
                idempotency_key=idem_key,
            )
        if resp.status != 200:
            return Response(status_code=resp.status)
        return JSONResponse(
            {
                "accepted": resp.accepted,
                "accept_id": resp.accept_id,
                "idempotent_replay": resp.idempotent_replay,
                "kind": resp.kind,
                "status": resp.applied_status,
                "bundle_url": resp.bundle_url,
                "pushed": resp.pushed,
            },
            status_code=200,
        )
