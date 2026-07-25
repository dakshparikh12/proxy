"""control_plane user-auth surface — Authlib + Google OIDC (Doc 00 §7).

User auth is Authlib's OAuth registry against Google's OpenID Connect discovery
document, configured by ``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``. The three
routes ``/auth/login``, ``/auth/callback``, ``/auth/logout`` are mounted here on
control_plane, plus a liveness ``/health`` probe. Both Authlib and the signed-
session middleware are imported lazily/guarded so the app object constructs even
when those optional deps are absent (the OIDC wire is confirmed at build); in a
real deployment the session cookie is signed with ``SESSION_SECRET``.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, RedirectResponse

# Google OpenID Connect discovery document (accounts.google.com well-known).
GOOGLE_OIDC_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


def _google_oauth() -> Any:
    """Build the Authlib OAuth registry for the Google OIDC (openid) client."""
    from authlib.integrations.starlette_client import OAuth  # lazy: adopt Authlib

    oauth = OAuth()
    oauth.register(
        name="google",
        server_metadata_url=GOOGLE_OIDC_DISCOVERY,
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def _install_signed_session(app: FastAPI) -> None:
    """Sign the session cookie with SESSION_SECRET (skipped if the dep is absent)."""
    try:
        from starlette.middleware.sessions import SessionMiddleware
    except ModuleNotFoundError:
        return
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ.get("SESSION_SECRET", "dev-only-unsigned"),
    )


async def _resolve_session_from_request(request: Request) -> dict[str, Any] | None:
    """Adapt the signed-session cookie into the ``{user_id, tenant_id}`` shape the
    §4.6 ``protected()`` wrapper reads — server-side, never a client-supplied field.

    The Authlib/session-middleware surface exposes the signed-in user on
    ``request.session["user"]`` (an OIDC ``userinfo`` dict). The tenant rides the
    session server-side; ``protected()`` 403s a session that carries no tenant and
    401s an absent/invalid session. A missing SessionMiddleware (an app built
    without the optional dep) reads as no session ⇒ 401 — fail-closed.
    """
    try:
        session = request.session.get("user")
    except (AssertionError, AttributeError):
        return None
    if not isinstance(session, dict):
        return None
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id") or session.get("email") or session.get("sub")
    if user_id is None:
        return None
    return {"user_id": user_id, "tenant_id": tenant_id}


def _stamp_internal_scoped(app: FastAPI) -> None:
    """Mark the ``/internal/*`` routes as internal-bearer-token-scoped (§4.6).

    These routes are gated by the ``X-Internal-Token`` header (a server-to-server
    trust plane, NEVER the user session cookie), so they are tenant-scoped by a
    different gate than ``protected()``. Stamping their endpoints lets the
    route-enumeration test classify them as scoped rather than raw — the test then
    still fails on any genuinely-unwrapped route.
    """
    from libs.http import mark_internal_scoped

    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if path.startswith("/internal/"):
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                mark_internal_scoped(endpoint)


def create_app() -> FastAPI:
    """Construct the control_plane ASGI app with the /auth routes + /health.

    The /internal route group (POST /internal/reconcile + GET /internal/notes/
    {meeting_id}) is mounted OUTSIDE the auth wall, gated by the internal bearer
    token; the authenticated GET /m/{meeting_id} user surface is mounted behind
    the wall. Both notes routes read the SAME note_deltas fold via the canonical
    ``scribe.notes_reader`` reader (DOC03-CSREAD — the cross-service notes read).
    """
    app = FastAPI(title="proxy-control-plane")
    _install_signed_session(app)

    # §4.6 safeError: an external caller (Recall, an anonymous connect-page visitor)
    # NEVER sees an internal error string — every non-validation error collapses to a
    # per-status fallback; a RequestValidationError returns the caller's own issues.
    from libs.http import install_safe_error_handler

    install_safe_error_handler(app)

    # The token-gated /internal notes + reconcile routes and the /m user surface.
    # Mounted here so the Workroom's cross-service notes read has a LIVE endpoint
    # alongside /internal/reconcile (closes the DOC03-CSREAD mount gap).
    from .accept_route import install_accept_route
    from .gateway_route import install_gateway_route
    from .internal import install_internal_routes

    install_internal_routes(app)
    # The §4.6 internal-token trust plane: the /internal/* routes are gated by the
    # X-Internal-Token header (a server-to-server bearer, NEVER the user session
    # cookie), so they are tenant-scoped by a different gate than protected(). Stamp
    # them so the route-enumeration test recognises them as scoped, not raw.
    _stamp_internal_scoped(app)
    # The authenticated draft-accept surface (§12.9): POST /m/{id}/drafts/{id}/accept
    # BEHIND the auth wall, reading durable storage (post-teardown safe). It declares
    # the §4.6 protected() wrapper so a fail-closed 401/403 fires server-side BEFORE
    # the handler — the marker the route-scope test reads to prove it is not raw.
    from libs.http import protected

    install_accept_route(app, dependencies=[protected(_resolve_session_from_request)])
    # The WS upgrade gateway (§4.3/§12.9): /ws authenticates at the connection UPGRADE —
    # an unauthenticated upgrade is rejected (401) BEFORE the 101, never per-message.
    install_gateway_route(app)

    @app.get("/health")
    async def health() -> Any:
        """Liveness probe — healthy while the process serves requests."""
        return JSONResponse({"status": "healthy"}, status_code=200)

    @app.get("/auth/login")
    async def auth_login(request: Request) -> Any:
        oauth = _google_oauth()
        redirect_uri = request.url_for("auth_callback")
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/callback")
    async def auth_callback(request: Request) -> Any:
        oauth = _google_oauth()
        token = await oauth.google.authorize_access_token(request)
        request.session["user"] = token.get("userinfo")
        return RedirectResponse(url="/")

    @app.get("/auth/logout")
    async def auth_logout(request: Request) -> Any:
        request.session.pop("user", None)
        return RedirectResponse(url="/")

    return app


app = create_app()
