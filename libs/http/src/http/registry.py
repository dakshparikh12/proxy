"""Contract-registry HTTP wrappers — the connect API's typed auth surface (§4.6).

The connect page's REST calls, the ``/m/{meeting_id}`` home (§2.8), and every
mutation register through typed wrappers where the handler receives a
credentials-only context and **never a raw** ``Request``/``Response`` — so "read
the tenant from the request body" is *unrepresentable*, not merely discouraged.

* :class:`AuthzCtx` — what a ``protected()`` handler gets. ``tenant_id`` is
  **non-null by construction**, so it is safe as a DB filter by *type*.
* :class:`PublicAuthzCtx` — what a public/dual-mode handler gets. ``tenant_id`` is
  **nullable ON PURPOSE** so it CANNOT be used as a DB filter by accident (a
  ``None`` tenant would silently widen a query to every tenant — the type refuses
  to let that compile as a filter without an explicit non-null check first).
* :func:`protected` — a FastAPI dependency that resolves the signed session
  server-side, 401s an anonymous caller, 403s a session with no tenant, and yields
  a fully-populated :class:`AuthzCtx`. The handler declares ``ctx = protected()``
  and receives credentials only; the raw request never reaches it.
* :data:`PUBLIC_ROUTES` — the ONLY routes reachable unauthenticated. The
  route-enumeration test (``tests/security/test_routes_are_scoped.py``) asserts
  every app route is either ``protected()``-scoped or listed here.

The structural guarantee (isolation triad, invariant 9): a client-supplied
``meeting_id`` is NEVER trusted to authorize an entity. ``protected()`` reads the
tenant off the server-side session — never a client field — and hands the handler
a ``tenant_id`` it cannot have forged.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from fastapi import Depends, HTTPException, Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from starlette.routing import BaseRoute

# The marker a ``protected()`` dependency carries so the route-enumeration test
# can recognise a tenant-scoped route by structure (not by name). Any route whose
# dependant tree contains a callable stamped with this attribute is scoped.
PROTECTED_DEP_MARKER = "__proxy_protected_dep__"

# A route registered through a non-session server-side trust plane (the internal
# bearer token, ``X-Internal-Token`` — NEVER the user session cookie). These are
# scoped, but by a different gate than ``protected()``; the enumeration test
# accepts them as scoped when the app stamps them via :func:`mark_internal_scoped`.
INTERNAL_SCOPED_MARKER = "__proxy_internal_scoped__"


@dataclass(frozen=True)
class AuthzCtx:
    """What an authenticated (``protected()``) handler gets — NOT req/res.

    ``tenant_id`` is **non-null by construction**: :func:`protected` 403s any
    session without a tenant before this is ever constructed, so a handler may use
    ``ctx.tenant_id`` directly as a DB filter with no null-guard and no risk of a
    query that silently spans tenants.
    """

    user_id: str
    tenant_id: str  # non-null by construction; safe as a DB filter by type


@dataclass(frozen=True)
class PublicAuthzCtx:
    """What a public / dual-mode handler gets — nullable ON PURPOSE.

    ``tenant_id`` is ``str | None``: a public caller has no session, so the tenant
    is unknown. It is nullable *by design* so it CANNOT be dropped into a query as
    a filter by accident — a ``None`` tenant used as a filter would widen the query
    to every tenant (a cross-tenant read). A dual-mode handler must first prove a
    scoped grant (a capability token) OR a non-null tenant + server-side
    membership check before it reads anything meeting-scoped.
    """

    user_id: Optional[str]
    tenant_id: Optional[str]  # nullable so it CANNOT be a DB filter by accident


# ---------------------------------------------------------------------------
# The public allowlist — the ONLY routes reachable unauthenticated.
#
# Each entry earns its exemption the way the webhook earns its (§4.6): it proves a
# scoped grant (HMAC signature / capability token / no-meeting-yet readiness poll),
# it is not trusted by default. The route-enumeration test asserts EVERY app route
# is either ``protected()``-scoped, internal-token-scoped, or in this set.
# ---------------------------------------------------------------------------
PUBLIC_ROUTES: frozenset[str] = frozenset(
    {
        "POST /webhooks/recall",  # Recall bot lifecycle — HMAC-signature-gated (§4.6)
        "POST /webhooks/github",  # GitHub push freshness ingress — X-Hub-Signature-256-gated (§3.6)
        "GET /connect/status",  # connect-page readiness poll (no meeting exists yet)
        "POST /connect/install/start",  # launch the GitHub-App install flow
        "GET /m/{meeting_id}",  # notes home — public ONLY with a valid capability token
        # (read-only notes for the forwarded-to recipient); a signed-in tenant
        # member takes the SAME route protected(). No token + no session ⇒ Not found.
        # Accept/reject are NOT here (protected()).
        "GET /health",  # liveness probe — no tenant data, no session (§0)
        # The OIDC sign-in flow itself is necessarily pre-session (it is how a
        # session is *obtained*); it carries no tenant data and returns redirects.
        "GET /auth/login",
        "GET /auth/callback",
        "GET /auth/logout",
    }
)


# A resolver takes the raw request and returns a principal or ``None``. Injected so
# ``protected()`` never imports a service (libs/http must not depend on services);
# ``mount_protected`` wires the live ``harness.resolve_session`` reader in.
SessionResolver = Callable[[Any], Awaitable[Optional[dict[str, Any]]]]


def protected(resolve_session: SessionResolver) -> Any:
    """A FastAPI dependency yielding an :class:`AuthzCtx` — or 401/403 fail-closed.

    ``resolve_session`` is the server-side session reader (the signed cookie →
    ``{user_id, tenant_id}`` map). An anonymous caller 401s; a session with no
    tenant 403s; only a fully-resolved principal produces an :class:`AuthzCtx`,
    whose ``tenant_id`` is therefore non-null by construction.

    The dependency function reads the raw request to resolve the session, but the
    handler declaring ``ctx = protected(...)`` receives ONLY the resulting
    :class:`AuthzCtx` — never the request. "Read the tenant from the body" is
    unrepresentable in the handler's signature.
    """

    async def _dep(request: Request) -> AuthzCtx:
        user = await resolve_session(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        tenant_id = user.get("tenant_id")
        if tenant_id is None:
            raise HTTPException(status_code=403, detail="No tenant assigned")
        return AuthzCtx(user_id=str(user.get("user_id")), tenant_id=str(tenant_id))

    setattr(_dep, PROTECTED_DEP_MARKER, True)
    return Depends(_dep)


def public() -> Any:
    """A FastAPI dependency yielding a :class:`PublicAuthzCtx` for a dual-mode route.

    Resolves the session best-effort: a signed-in tenant member gets a populated
    context (so the same route serves them ``protected()``-equivalent data via a
    server-side membership check), while an anonymous caller gets ``None`` fields.
    The nullable ``tenant_id`` is the type that stops an accidental cross-tenant
    read. A route using this MUST be in :data:`PUBLIC_ROUTES` (it is reachable
    unauthenticated by design) — the enumeration test enforces that.
    """

    async def _dep(request: Request) -> PublicAuthzCtx:
        return PublicAuthzCtx(user_id=None, tenant_id=None)

    return Depends(_dep)


# ---------------------------------------------------------------------------
# Route classification — the machinery the enumeration test reads.
# ---------------------------------------------------------------------------
def route_key(route: "BaseRoute") -> Optional[str]:
    """``"METHOD /path"`` for an app route, or ``None`` for a non-HTTP route.

    A websocket route (no HTTP methods) and framework scaffolding without a stable
    method are returned as ``None`` so the caller can classify them separately.
    HEAD is a framework-added twin of GET and is ignored for keying.
    """
    methods = getattr(route, "methods", None)
    path = getattr(route, "path", None)
    if not methods or path is None:
        return None
    verbs = sorted(m for m in methods if m != "HEAD")
    if not verbs:
        return None
    return f"{verbs[0]} {path}"


def declares_protected_dep(route: "BaseRoute") -> bool:
    """True iff the route's dependency tree contains a ``protected()`` dependency.

    Walks the FastAPI ``dependant`` graph looking for a dependency callable stamped
    with :data:`PROTECTED_DEP_MARKER`. Structural, not name-based: a route is
    scoped because it *declares the wrapper*, not because it looks authenticated.
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    seen: set[int] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        call = getattr(node, "call", None)
        if call is not None and getattr(call, PROTECTED_DEP_MARKER, False):
            return True
        stack.extend(getattr(node, "dependencies", []) or [])
    return False


def is_internal_scoped(route: "BaseRoute") -> bool:
    """True iff the route is stamped as internal-bearer-token-scoped.

    The ``/internal/*`` routes are gated by the ``X-Internal-Token`` header — a
    server-to-server trust plane, never the user session cookie. They are scoped,
    but by a different gate than ``protected()``; :func:`mark_internal_scoped`
    stamps them so the enumeration test accepts them as scoped, not raw.
    """
    endpoint = getattr(route, "endpoint", None)
    return bool(endpoint is not None and getattr(endpoint, INTERNAL_SCOPED_MARKER, False))


def mark_internal_scoped(fn: Any) -> Any:
    """Stamp a route endpoint as internal-bearer-token-scoped (decorator/util)."""
    setattr(fn, INTERNAL_SCOPED_MARKER, True)
    return fn


def is_websocket_route(route: "BaseRoute") -> bool:
    """True for a websocket route (authorised at the connection UPGRADE, §4.3)."""
    return type(route).__name__ in {"WebSocketRoute", "APIWebSocketRoute"}


def is_framework_route(route: "BaseRoute") -> bool:
    """True for FastAPI scaffolding (``/openapi.json``, ``/docs``, ``/redoc``).

    These are the framework's own docs/schema routes, not app surface; they expose
    no tenant data. They are outside the tenant-scope question by construction.
    """
    path = getattr(route, "path", "") or ""
    return path in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def classify_route(route: "BaseRoute") -> str:
    """Classify a route as ``protected`` | ``public`` | ``internal`` | ``ws`` |
    ``framework`` | ``raw`` — the single verdict the enumeration test asserts on.

    ``raw`` is the failure class: a route that registers an HTTP surface without a
    wrapper and is not on the allowlist. Every other class is accounted for.
    """
    if is_framework_route(route):
        return "framework"
    if is_websocket_route(route):
        return "ws"
    if declares_protected_dep(route):
        return "protected"
    if is_internal_scoped(route):
        return "internal"
    key = route_key(route)
    if key is not None and key in PUBLIC_ROUTES:
        return "public"
    return "raw"


__all__ = [
    "AuthzCtx",
    "PublicAuthzCtx",
    "PUBLIC_ROUTES",
    "PROTECTED_DEP_MARKER",
    "INTERNAL_SCOPED_MARKER",
    "SessionResolver",
    "protected",
    "public",
    "route_key",
    "declares_protected_dep",
    "is_internal_scoped",
    "mark_internal_scoped",
    "is_websocket_route",
    "is_framework_route",
    "classify_route",
]
