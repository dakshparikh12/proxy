"""control_plane user-auth surface — Authlib + Google OIDC (Doc 00 §7).

User auth is Authlib's OAuth registry against Google's OpenID Connect discovery
document, configured by ``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``. The three
routes ``/auth/login``, ``/auth/callback``, ``/auth/logout`` are mounted here on
control_plane. Both Authlib and the signed-session middleware are imported
lazily/guarded so the app object constructs even when those optional deps are
absent (the OIDC wire is confirmed at build); in a real deployment the session
cookie is signed with ``SESSION_SECRET``.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse

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


def create_app() -> FastAPI:
    """Construct the control_plane ASGI app with the three /auth routes."""
    app = FastAPI(title="proxy-control-plane")
    _install_signed_session(app)

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
