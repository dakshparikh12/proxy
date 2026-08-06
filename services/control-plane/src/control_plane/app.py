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

# The durable session cookie name. It carries the HMAC-signed ``sessions``-row id
# that ``control_plane.session.resolve_session`` reads (the single source of truth is the
# DB row, not the cookie). This MUST match the name ``resolve_session`` /
# ``complete_signin`` already use — do not invent a new scheme.
SESSION_COOKIE = "session"


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
    # Prefer the DURABLE session — the HMAC 'session' cookie ``auth_callback`` writes via
    # ``complete_signin`` (the sessions row is the source of truth the WS gateway + /m read).
    # The real OAuth flow produces ONLY this cookie, so reading it is what lets a signed-in
    # member accept/reject a draft (before this, protected() read the never-populated
    # SessionMiddleware dict → every real member 401'd on the Law-3 accept path).
    db = getattr(request.app.state, "db", None)
    if db is not None:
        try:
            from control_plane.session import resolve_session

            resolved = await resolve_session(db, request.cookies)
        except Exception:  # noqa: BLE001 - a resolution fault falls through to the middleware read
            resolved = None
        if isinstance(resolved, dict) and resolved.get("user_id") is not None:
            return {"user_id": resolved["user_id"], "tenant_id": resolved.get("tenant_id")}
    # Fallback: the Starlette SessionMiddleware dict — retained for back-compat with a caller
    # that carries a middleware session cookie (no durable row). Fail-closed to None.
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


def create_app() -> FastAPI:
    """Construct the control_plane ASGI app with the /auth routes + /health.

    The authenticated GET /m/{meeting_id} user surface is mounted behind the auth
    wall. (The old /internal notes+reconcile route group and its scribe note_deltas
    fold were removed in the reactive-workroom pivot — the new system has no scribe
    note_deltas pipeline for those routes to read.)
    """
    app = FastAPI(title="proxy-control-plane")
    _install_signed_session(app)

    # §4.6 safeError: an external caller (Recall, an anonymous connect-page visitor)
    # NEVER sees an internal error string — every non-validation error collapses to a
    # per-status fallback; a RequestValidationError returns the caller's own issues.
    from libs.http import install_safe_error_handler

    install_safe_error_handler(app)

    # The /m user surface + the accept/reject/webhook routes.
    from .accept_route import install_accept_route, install_reject_route
    from .meeting_home import install_meeting_home_route
    from .webhook_routes import install_recall_webhook_route

    # §2.8: the authenticated dual-mode per-meeting home GET /m/{meeting_id}. It
    # renders that meeting's staged-draft cards (§2.4 #8) for a signed-in tenant member —
    # behind a SERVER-SIDE meeting→tenant check (a cross-tenant member gets Not
    # found). A valid capability token gives a notes-ONLY view (NO drafts, Law 3);
    # no session + no token → Not found. It is in PUBLIC_ROUTES (public only via a
    # valid token) — the enumeration test accepts it as public by that allowlist.
    install_meeting_home_route(app)
    # §4.6: the Recall webhook receiver — PUBLIC_ROUTES-allowlisted but HMAC-gated.
    # The signature is verified over the RAW body via a constant-time compare BEFORE
    # the durable webhook_events insert, so a forged/missing signature is a 401 with
    # NO row landed (a forged delivery can never dedupe-poison the table).
    install_recall_webhook_route(app)
    # §3.6: the LIVE GitHub push webhook ingress — PUBLIC_ROUTES-allowlisted but HMAC-gated
    # (X-Hub-Signature-256, verified over the RAW body BEFORE any rebuild). This is the LIVE
    # caller of the freshness WebhookHandler: a verified push is routed by its SIGNED
    # repository (resolved server-side to the pipeline the connect trigger registered) into
    # WebhookHandler.handle, which dedups + pulls the delta once + does a full drop/re-extract
    # + invalidates caches + notifies live meetings. Closes the freshness live-wiring gap so
    # the push→reindex path is real, not isolation-only. A forged/missing signature is a 401
    # that triggers NO rebuild; the tenant is NEVER read from the request.
    from .github_webhook import install_github_webhook_route

    install_github_webhook_route(app)
    # The authenticated draft-accept surface (§12.9): POST /m/{id}/drafts/{id}/accept
    # BEHIND the auth wall, reading durable storage (post-teardown safe). It declares
    # the §4.6 protected() wrapper so a fail-closed 401/403 fires server-side BEFORE
    # the handler — the marker the route-scope test reads to prove it is not raw. The
    # mount defaults its audit_sink to the durable audit-log channel, so EVERY real
    # green accept POST records the world-touching action (§2.8 audit, a hard DoD req).
    from libs.http import protected

    install_accept_route(app, dependencies=[protected(_resolve_session_from_request)])
    # The symmetric draft-REJECT surface (§2.8/§12.9): POST /m/{id}/drafts/{id}/reject
    # BEHIND the SAME auth wall. It declares the §4.6 protected() wrapper (so a
    # capability token can NEVER reach it — reject is the other half of the one
    # world-touching pair, Law 3) and flips the durable row to 'rejected' (applies
    # nothing, never pushes). A separate protected() instance per route keeps each
    # route's dependant tree independently stamped for the enumeration test.
    install_reject_route(app, dependencies=[protected(_resolve_session_from_request)])
    # The hosted invite route (the front door): POST /meetings — "give Proxy a
    # meeting URL" over HTTP. BEHIND the SAME §4.6 protected() wall (the handler
    # receives a credentials-only AuthzCtx; the tenant NEVER rides the body), it
    # proves the named repo belongs to the caller's tenant, pins HEAD from the
    # durable repo_maps index, and drives the EXISTING invite_proxy — the meetings
    # row bound to (tenant, repo, pinned_sha=HEAD) + the REAL Recall bot launch
    # through the transport seam. 201 {meeting_id, bot_id}.
    from .meetings_route import install_meetings_route

    install_meetings_route(app, session_resolver=_resolve_session_from_request)
    # The MONITORED-smoke taps (dev-only, gated by the internal admin bearer, NOT a user session):
    # POST /admin/test-provision drives the REAL invite_proxy without the Google-OAuth wall so a
    # headless smoke can put Proxy into a real Meet; GET /admin/transcript surfaces the live sandbox
    # MEETING_NOTES.md so the harness can SEE what Proxy heard. Both fail CLOSED (401) unless
    # PROXY_INTERNAL_TOKEN is provisioned, so they are inert on any process without that bearer.
    from .dev_smoke_routes import install_dev_smoke_routes

    install_dev_smoke_routes(app)
    # The in-sandbox meeting MCP relay receiver (SPEC §4/§5): POST /meetings/{id}/relay. Native
    # Claude in the per-meeting sandbox reaches the live room through its ONE ``to_meeting`` tool;
    # the in-sandbox MCP server POSTs each call here, authenticated by the per-meeting relay bearer
    # minted at join (a server-to-server trust plane, NOT a user session — stamped internal-scoped
    # like /internal/*). The route lands the call on the meeting's live ``MeetingConnection`` (real
    # Recall/Cartesia creds, host-side). Never-throw: a forged/misdirected relay is an honest error
    # JSON, never a crash.
    from .relay import install_relay_route

    install_relay_route(app)
    # The connect page's two PUBLIC REST routes (§2.7/§4.6): GET /connect/status (the
    # readiness poll — REST, not a WS message, CANONICAL §12.12) and POST
    # /connect/install/start (launch the GitHub-App install AND fire the connect→index
    # trigger — the first live run_full_pipeline caller). Both are on the PUBLIC_ROUTES
    # allowlist (no meeting exists yet) and validated like any public API.
    from .connect import install_connect_routes

    install_connect_routes(app)
    # THE CUTOVER: the Output-Media surface — the orb page + per-meeting audio feed the
    # Recall bot streams as its camera (``GET /output-media/{meeting_id}`` + its WS).
    # ``RECALL_OUTPUT_MEDIA_URL`` points HERE at deploy (the join config already sends it
    # on bot create). Recall's headless browser has no session, so the page route is
    # PUBLIC_ROUTES-allowlisted (the page is the orb shell — no tenant data; the WS feed
    # carries only Proxy's own synthesized speech for that meeting id, keyed on the
    # unguessable meeting uuid) and the WS route classifies ``ws`` for the enumeration gate.
    # Mounted as MATERIALIZED routes (not ``app.include_router``): this FastAPI version's
    # lazy include leaves an ``_IncludedRouter`` marker in ``app.routes`` that the §4.6
    # structural route-enumeration cannot classify — appending the router's real
    # APIRoute/APIWebSocketRoute objects (empty prefix) is the classic include semantics
    # and keeps every mounted route a first-class, enumerable object.
    from in_meeting import output_media

    for _media_route in output_media.router.routes:
        app.router.routes.append(_media_route)

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
        userinfo = token.get("userinfo") or {}
        response = RedirectResponse(url="/")
        # Converge the two session mechanisms onto the DURABLE session. The OIDC
        # userinfo carries the verified email; complete_signin creates/loads the
        # users + tenants rows and the durable ``sessions`` row, and returns the
        # HMAC-signed cookie that ``resolve_session`` reads — so a user who signs
        # in over HTTP is authenticated on the WS ``/ws`` gateway and every
        # ``resolve_session`` surface (before this, HTTP sign-in minted NO
        # ``sessions`` row and those surfaces could not see them). The DB row is
        # the single source of truth; the cookie only carries its id.
        #
        # We DELIBERATELY do NOT write ``request.session["user"]`` here: the
        # Starlette SessionMiddleware and this durable cookie both use the cookie
        # name ``session`` (the name resolve_session + the sealed WS/oracle tests
        # pin), so writing the middleware session would emit a SECOND, colliding
        # ``Set-Cookie: session=`` that a browser resolves last-wins — clobbering
        # the durable cookie and breaking resolve_session. One cookie, one source
        # of truth. (The /m + draft surfaces that read request.session["user"]
        # keep their own mechanism; nothing here mutates it.)
        email = userinfo.get("email")
        db = getattr(request.app.state, "db", None)
        if email and db is not None:
            from control_plane.session import complete_signin

            result = await complete_signin(db, email=email)
            response.set_cookie(
                SESSION_COOKIE, result.cookie, httponly=True, samesite="lax"
            )
        return response

    @app.get("/auth/logout")
    async def auth_logout(request: Request) -> Any:
        request.session.pop("user", None)
        response = RedirectResponse(url="/")
        # Complete logout: delete the durable ``sessions`` row (the source of
        # truth) AND clear the ``session`` cookie so no ``resolve_session`` surface
        # resolves the signed-out user any longer.
        db = getattr(request.app.state, "db", None)
        if db is not None:
            from control_plane.session import logout_session

            await logout_session(db, request.cookies)
        response.delete_cookie(SESSION_COOKIE)
        return response

    return app


app = create_app()
