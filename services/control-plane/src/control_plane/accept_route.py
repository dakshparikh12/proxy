"""POST /m/{meeting_id}/drafts/{draft_id}/accept — the human-approval accept route.

Accepting a staged draft is the one world-touching click (Law 3), so the route is
hardened (§3.16.1, CANONICAL §12.9): an unauthenticated caller is rejected; an
invalid CSRF token is rejected; a member of a DIFFERENT tenant is rejected by a
SERVER-SIDE draft->meeting->tenant check (a client-supplied tenant is NEVER trusted);
a correct tenant member succeeds and the SAME idempotency key replays the first result
instead of double-applying; and every accept is audited with the acting tenant member —
the route mounts install a default audit sink (the durable audit-log channel), so the
audit fires on the LIVE route path, not only when a caller hand-passes an ``audit_sink``.

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

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# Module-scope Starlette request/response types: the route mounts annotate their
# handlers with these, and ``from __future__ import annotations`` stringizes those
# annotations — FastAPI resolves them via the MODULE globals, so ``Request`` MUST live
# here (a function-local import leaves it unresolvable and FastAPI misreads ``request``
# as a query param → a spurious 422 on the first real POST). Matches the module-scope
# convention every other control_plane route mount (internal/meeting_home/connect) uses.
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import accept as _accept
from . import authz as _authz

if TYPE_CHECKING:
    from fastapi import FastAPI

# The audit trail for the one world-touching pair (accept/reject). A structured line
# on this channel IS the durable audit record in deployment (Cloud Logging), so the
# DoD's "a world-touching action is recorded" holds on the LIVE route path — not only
# when a caller hand-passes an ``audit_sink`` (§2.8, CANONICAL §12.9).
_AUDIT_LOG = logging.getLogger("services.control_plane.audit")


def _default_audit_sink(record: Any) -> None:
    """Record a world-touching accept/reject on the durable audit channel.

    The default sink the LIVE route mounts install so audit fires on every real POST.
    It logs the acting-tenant-member record at INFO (never a secret — the record
    carries only meeting/draft/tenant/user/kind/id). A test may inject a capturing
    sink instead to bind + assert the audit property directly.
    """
    _AUDIT_LOG.info("%s", record)


# D-039 — DURABLE, cross-instance idempotency. The world-touching apply is idempotent on
# the durable ``staged_drafts`` terminal-status belt (``apply_accepted_draft`` /
# ``reject_staged_draft`` short-circuit an ``applied``/``rejected`` row → ``already_applied``),
# so a retry NEVER double-applies on ANY control_plane instance — not just the one holding a
# process-local ledger. The accept/reject id is DERIVED from the durable (action, meeting,
# draft), so a replay on a different Cloud Run instance returns the IDENTICAL id and the one
# world-touching click audits exactly once. No process-local dict, no schema change.
def _deterministic_id(action: str, meeting_id: Any, draft_id: Any) -> str:
    """The stable accept/reject id for a durable (action, meeting, draft) — identical across
    every instance + every retry (the replay returns the first id; §3.16.1 / §12.9 / D-039)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"proxy:{action}:{meeting_id}:{draft_id}").hex


# Retained ONLY as a compatibility surface for the recycle-simulation tests (which
# ``.clear()`` these to prove idempotency survives a lost in-memory ledger). The handlers no
# longer read or write them — idempotency is now entirely durable (the ``staged_drafts``
# terminal-status belt + the deterministic id above), so clearing these is a no-op and the
# durable belt still prevents a double-apply. D-039: no process-local idempotency state.
_ACCEPTS: dict[tuple[str, str, str], "AcceptResponse"] = {}
_REJECTS: dict[tuple[str, str, str], "AcceptResponse"] = {}


@dataclass(frozen=True)
class AcceptResponse:
    """The accept/reject route's typed response (shared shape, symmetric twins)."""

    status: int
    accepted: bool = False
    rejected: bool = False
    accept_id: str | None = None
    reject_id: str | None = None
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

    # (4+5) Apply from DURABLE storage (kind-aware: notes-edit apply vs code-change
    #       record+expose, never push). Idempotency is DURABLE + cross-instance (D-039):
    #       apply_accepted_draft's staged_drafts terminal-status belt short-circuits a replay
    #       (already_applied=True), so a retry NEVER double-applies — on ANY control_plane
    #       instance, not just the one that minted the response. ``idempotency_key`` is
    #       accepted for API compatibility; the durable row status is the real witness.
    try:
        applied = _accept.apply_accepted_draft(
            conn, meeting_id=meeting_id, draft_id=draft_id
        )
    except LookupError:
        return AcceptResponse(status=404, rejected=True)

    # Deterministic accept id → a replay on any instance returns the IDENTICAL id.
    accept_id = _deterministic_id("accept", meeting_id, draft_id)
    response = AcceptResponse(
        status=200,
        accepted=True,
        accept_id=accept_id,
        idempotent_replay=applied.already_applied,
        kind=applied.kind,
        applied_status=applied.applied_status,
        bundle_url=applied.bundle_url,
        pushed=applied.pushed,
    )

    # (6) Audit the REAL world-touching apply exactly once — never re-audit a durable replay.
    if audit_sink is not None and not applied.already_applied:
        audit_sink(
            f"accept meeting={meeting_id} draft={draft_id} "
            f"tenant={getattr(request, 'tenant', None)} "
            f"user={getattr(request, 'user', None)} "
            f"kind={applied.kind} accept_id={accept_id}"
        )
    return response


def handle_reject(
    conn: Any,
    *,
    request: Any,
    meeting_id: str,
    draft_id: str,
    idempotency_key: str,
    audit_sink: Callable[[Any], None] | None = None,
) -> AcceptResponse:
    """Authorize + decline a draft on DURABLE storage (idempotent, audited).

    The symmetric twin of :func:`handle_accept` (spec §2.8, CANONICAL §12.9). Same
    fail-closed order — auth → CSRF → server-side draft→meeting→tenant → (replay?
    return first) → decline → audit — but the terminal action flips the durable row
    to ``rejected`` and applies NOTHING (no notes-edit, no push). ``conn`` is a durable
    (sync psycopg) connection: a reject can arrive long after the meeting harness is
    gone, so it runs on durable storage, never the dead in-memory review session.
    """
    # (1) Authentication: an unauthenticated caller cannot reject.
    if not _authenticated(request):
        return AcceptResponse(status=401, rejected=True)

    # (2+3) CSRF + SERVER-SIDE draft→meeting→tenant barrier (same gate as accept — the
    #        owning tenant is derived from the persisted row, never a client field).
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
        # A different tenant OR an unknown draft — refused, and NOTHING is changed.
        return AcceptResponse(status=403, rejected=True)

    # (4+5) Decline on DURABLE storage: flip the row to 'rejected', apply/push nothing.
    #       Idempotency is DURABLE + cross-instance (D-039): reject_staged_draft's terminal
    #       belt short-circuits a replay (already_applied=True) so a retry never double-rejects
    #       on any instance. ``idempotency_key`` is accepted for API compatibility; the durable
    #       row status is the real witness.
    try:
        declined = _accept.reject_staged_draft(
            conn, meeting_id=meeting_id, draft_id=draft_id
        )
    except LookupError:
        return AcceptResponse(status=404, rejected=True)

    # Deterministic reject id → a replay on any instance returns the IDENTICAL id.
    reject_id = _deterministic_id("reject", meeting_id, draft_id)
    response = AcceptResponse(
        status=200,
        rejected=True,
        accepted=False,
        reject_id=reject_id,
        idempotent_replay=declined.already_applied,
        kind=declined.kind,
        applied_status=declined.applied_status,
        pushed=declined.pushed,
    )

    # (6) Audit the REAL world-touching decline exactly once — never re-audit a durable replay.
    if audit_sink is not None and not declined.already_applied:
        audit_sink(
            f"reject meeting={meeting_id} draft={draft_id} "
            f"tenant={getattr(request, 'tenant', None)} "
            f"user={getattr(request, 'user', None)} "
            f"kind={declined.kind} reject_id={reject_id}"
        )
    return response


# ── The authenticated control_plane route mount ──────────────────────────────
ACCEPT_PATH = "/m/{meeting_id}/drafts/{draft_id}/accept"
REJECT_PATH = "/m/{meeting_id}/drafts/{draft_id}/reject"


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


async def _principal_and_key(request: Any) -> "tuple[_AuthzedRequest | None, str]":
    """Derive the SERVER-SIDE principal (+ idempotency key) for a mutation route.

    Shared by the accept and reject route mounts so both derive the tenant/CSRF the
    SAME way: the tenant rides the signed session server-side (never a client body
    field), the CSRF is the double-submit header==cookie compare, and the idempotency
    key is the ``Idempotency-Key`` header. Returns ``(None, "")`` when there is no
    session (the caller maps that to a fail-closed 401) — a missing SessionMiddleware
    (an app built without the optional dep) reads as no session.
    """
    # Prefer the DURABLE session (the 'session' cookie ``auth_callback`` writes via
    # ``complete_signin``) so a real OAuth-signed-in member can derive a principal on the
    # Law-3 accept/reject path; fall back to the SessionMiddleware dict for a middleware-cookie
    # caller. Fail-closed to no-session → 401.
    session: Any = None
    db = getattr(request.app.state, "db", None)
    if db is not None:
        try:
            from control_plane.session import resolve_session

            resolved = await resolve_session(db, request.cookies)
        except Exception:  # noqa: BLE001 - a resolution fault falls through to the middleware read
            resolved = None
        if isinstance(resolved, dict) and resolved.get("user_id") is not None:
            session = {"tenant_id": resolved.get("tenant_id"), "email": resolved.get("user_id")}
    if session is None:
        try:
            session = request.session.get("user")
        except (AssertionError, AttributeError):
            session = None  # no SessionMiddleware installed -> treated as no session
    if not session:
        return None, ""

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
    return principal, idem_key


def install_accept_route(
    app: "FastAPI",
    *,
    dependencies: "list[Any] | None" = None,
    audit_sink: "Callable[[Any], None] | None" = None,
) -> None:
    """Mount POST /m/{meeting_id}/drafts/{draft_id}/accept BEHIND the auth wall.

    The route resolves the durable connection off ``app.state.db`` (a missing handle
    is an honest 503, never a fabricated 200), derives the principal + tenant from the
    signed session server-side, checks the CSRF header, and delegates to
    :func:`handle_accept`. The response status is the handler's status verbatim.

    ``dependencies`` (a list of FastAPI ``Depends``) is declared at the route so the
    §4.6 ``protected()`` wrapper's server-side auth gate fires BEFORE the handler —
    a fail-closed 401/403 for an anonymous or tenant-less caller. It is the marker
    the ``tests/security/test_routes_are_scoped.py`` enumeration reads to prove this
    mutation is tenant-scoped, not raw. The handler keeps its own session/CSRF/
    server-side tenant checks as defense-in-depth (the accept is the one
    world-touching click, Law 3).

    ``audit_sink`` is the world-touching-action recorder threaded into
    :func:`handle_accept` so every REAL green POST is audited (§2.8, CANONICAL §12.9,
    a hard DoD requirement). It defaults to :func:`_default_audit_sink` (the durable
    audit-log channel) — so the LIVE mount audits by default; a test may inject a
    capturing sink to bind + assert the audit record on the real route path.

    ``app`` is the concrete :class:`fastapi.FastAPI` ``create_app`` builds; the
    annotation gives ``app.post`` a typed signature so the route decorator is a
    typed decorator under ``mypy --strict`` (an ``app: Any`` would make the mounted
    handler untyped — ``[untyped-decorator]``).
    """
    sink = audit_sink if audit_sink is not None else _default_audit_sink

    @app.post(ACCEPT_PATH, include_in_schema=True, dependencies=dependencies or [])
    async def accept_draft_route(meeting_id: str, draft_id: str, request: Request) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return Response(status_code=503)  # no durable substrate handle -> honest 503

        principal, idem_key = await _principal_and_key(request)
        if principal is None:
            return Response(status_code=401)

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
                audit_sink=sink,
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


def install_reject_route(
    app: "FastAPI",
    *,
    dependencies: "list[Any] | None" = None,
    audit_sink: "Callable[[Any], None] | None" = None,
) -> None:
    """Mount POST /m/{meeting_id}/drafts/{draft_id}/reject BEHIND the auth wall.

    The symmetric twin of :func:`install_accept_route` (spec §2.8, CANONICAL §12.9):
    same ``protected()`` wall (via ``dependencies``), same server-side session/tenant/
    CSRF derivation, same durable-connection acquire, same defaulted ``audit_sink`` so
    a REAL green reject POST is audited on the LIVE path — but it delegates to
    :func:`handle_reject`, which flips the persisted row to ``rejected`` and applies
    NOTHING (no notes-edit, no push). A missing substrate handle is an honest 503; an
    anonymous caller is a fail-closed 401 (defense-in-depth behind the ``protected()``
    dependency, which already 401/403s an anonymous/tenant-less caller server-side).

    ``dependencies`` declares the §4.6 ``protected()`` wrapper so the route classifies
    ``protected`` (never ``raw``) and a capability token can NEVER reach it — reject is
    the other half of the one world-touching pair, and a token grants notes-read only
    (Law 3). ``app`` is the concrete :class:`fastapi.FastAPI` so the decorator is typed
    under ``mypy --strict``.
    """
    sink = audit_sink if audit_sink is not None else _default_audit_sink

    @app.post(REJECT_PATH, include_in_schema=True, dependencies=dependencies or [])
    async def reject_draft_route(meeting_id: str, draft_id: str, request: Request) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return Response(status_code=503)  # no durable substrate handle -> honest 503

        principal, idem_key = await _principal_and_key(request)
        if principal is None:
            return Response(status_code=401)

        async with db.acquire() as aconn:  # noqa: F841 - async pool handle
            resp = handle_reject(
                aconn,
                request=principal,
                meeting_id=meeting_id,
                draft_id=draft_id,
                idempotency_key=idem_key,
                audit_sink=sink,
            )
        if resp.status != 200:
            return Response(status_code=resp.status)
        return JSONResponse(
            {
                "rejected": resp.rejected,
                "reject_id": resp.reject_id,
                "idempotent_replay": resp.idempotent_replay,
                "kind": resp.kind,
                "status": resp.applied_status,
                "pushed": resp.pushed,
            },
            status_code=200,
        )
