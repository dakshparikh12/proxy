"""Doc 00 §7 — the HTTP OAuth callback and the durable session store converge.

The gap this closes: ``/auth/callback`` used to set ONLY the Starlette
``SessionMiddleware`` cookie (``request.session["user"]``) and NEVER called
``complete_signin`` — so a user who signed in via HTTP had NO ``sessions`` DB row,
and every surface that authenticates via ``resolve_session`` (the WS ``/ws`` gateway
and the ``/m`` read surfaces) could not see them. This test proves the two mechanisms
now share state: after the callback runs, the cookie it sets resolves via
``resolve_session`` to the correct ``{user_id, tenant_id}``.

Product imports live inside the test bodies so the module COLLECTS clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Reuse the doc00 test-support helpers (local DSN + migrations + pg conn).
_SUPPORT_DIR = Path(__file__).resolve().parents[1] / "doc00"
if str(_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(_SUPPORT_DIR))
import _support as S  # noqa: E402


@pytest.mark.integration
def test_auth_callback_populates_the_durable_session_resolve_reads():
    """Driving the REAL /auth/callback handler mints a durable session that
    ``resolve_session`` (the WS-gateway / ``/m`` auth path) then resolves.
    """
    import asyncio

    import importlib

    from libs.db import Database

    dsn = S._local_dsn()
    if not dsn:
        pytest.skip("no local Postgres")
    r = S.apply_migrations(dsn)
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

    app = importlib.import_module("control_plane.app").create_app()

    async def _flow() -> tuple[str, dict | None]:
        db = await Database.connect(dsn)
        app.state.db = db
        try:
            # A stand-in for the Authlib request whose OAuth exchange already
            # produced the OIDC userinfo (carrying the verified email). We drive
            # the callback's OWN logic through the app's real signature by faking
            # only the token exchange, exactly as the live callback receives it.
            app_mod = importlib.import_module("control_plane.app")

            captured: dict[str, str] = {}

            class _FakeGoogle:
                async def authorize_access_token(self, request):  # noqa: ANN001, ARG002
                    return {"userinfo": {"email": "callback@example.com", "sub": "g-1"}}

            class _FakeOAuth:
                google = _FakeGoogle()

            orig = app_mod._google_oauth
            app_mod._google_oauth = lambda: _FakeOAuth()  # type: ignore[assignment]

            class _Session(dict):
                pass

            class _Req:
                def __init__(self) -> None:
                    self.app = app
                    self.session = _Session()

            try:
                # Locate the callback endpoint on the real app and invoke it.
                callback = next(
                    route.endpoint  # type: ignore[attr-defined]
                    for route in app.routes
                    if getattr(route, "path", None) == "/auth/callback"
                )
                resp = await callback(_Req())
            finally:
                app_mod._google_oauth = orig  # type: ignore[assignment]

            # The callback set the durable HMAC session cookie the other surfaces read.
            set_cookie = resp.raw_headers
            cookie_val = None
            for k, v in set_cookie:
                if k.lower() == b"set-cookie" and v.lower().startswith(b"session="):
                    cookie_val = v.decode().split("session=", 1)[1].split(";", 1)[0]
            captured["cookie"] = cookie_val or ""

            from control_plane.session import resolve_session

            resolved = await resolve_session(db, {"session": captured["cookie"]})
            return captured["cookie"], resolved
        finally:
            await db.close()

    cookie, resolved = asyncio.run(_flow())
    assert cookie, "the callback must set a durable 'session' cookie (HMAC-signed)"
    assert resolved is not None, (
        "resolve_session must now authenticate the HTTP-signed-in user — the WS gateway "
        "and /m surfaces would have failed before this convergence"
    )
    assert resolved["user_id"] is not None and resolved["tenant_id"] is not None

    # The durable sessions row exists and belongs to the signed-in user.
    with S.pg_conn() as conn:
        n = conn.execute(
            "SELECT count(*) FROM users WHERE email='callback@example.com'"
        ).fetchone()[0]
        assert n == 1, "sign-in via the callback must create exactly one users row"

    # A tampered cookie must NOT resolve (the HMAC oracle still holds).
    async def _tampered() -> dict | None:
        db = await Database.connect(dsn)
        try:
            from control_plane.session import resolve_session

            return await resolve_session(db, {"session": cookie + "TAMPER"})
        finally:
            await db.close()

    assert not asyncio.run(_tampered()), "a tampered session cookie must not resolve"


@pytest.mark.integration
def test_logout_clears_the_durable_session():
    """After logout, the durable session cookie no longer resolves — logout is complete."""
    import asyncio

    from libs.db import Database

    dsn = S._local_dsn()
    if not dsn:
        pytest.skip("no local Postgres")
    r = S.apply_migrations(dsn)
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

    async def _flow() -> dict | None:
        db = await Database.connect(dsn)
        try:
            from control_plane.session import (
                complete_signin,
                logout_session,
                resolve_session,
            )

            signin = await complete_signin(db, email="logout@example.com")
            # Sanity: it resolves before logout.
            before = await resolve_session(db, {"session": signin.cookie})
            assert before is not None, "the session must resolve before logout"

            await logout_session(db, {"session": signin.cookie})
            return await resolve_session(db, {"session": signin.cookie})
        finally:
            await db.close()

    after = asyncio.run(_flow())
    assert after is None, "logout must delete the durable session so it no longer resolves"


def test_callback_emits_exactly_one_session_cookie_the_durable_hmac_one():
    """Through a real HTTP round-trip the browser keeps EXACTLY ONE ``session``
    cookie — the durable HMAC cookie, NOT a colliding Starlette middleware blob.

    This guards the collision that would silently defeat the fix: both mechanisms
    use the cookie name ``session``; if the callback also wrote
    ``request.session["user"]`` the middleware would append a SECOND
    ``Set-Cookie: session=`` that a browser resolves last-wins, clobbering the
    durable cookie so ``resolve_session`` would never see it. No live DB needed —
    ``complete_signin`` is stubbed to a deterministic HMAC-shaped cookie.
    """
    import importlib

    from starlette.testclient import TestClient

    app_mod = importlib.import_module("control_plane.app")

    durable = "22222222-2222-2222-2222-222222222222.deadbeefmac"

    class _FakeGoogle:
        async def authorize_access_token(self, request):  # noqa: ANN001, ARG002
            return {"userinfo": {"email": "one@example.com", "sub": "g-2"}}

    class _FakeOAuth:
        google = _FakeGoogle()

    class _Res:
        cookie = durable
        user_id = "u"
        tenant_id = "t"

    async def _fake_signin(db, *, email):  # noqa: ANN001, ANN202, ARG001
        return _Res()

    import control_plane.session as HS

    orig_oauth = app_mod._google_oauth
    orig_signin = HS.complete_signin
    app_mod._google_oauth = lambda: _FakeOAuth()  # type: ignore[assignment]
    HS.complete_signin = _fake_signin  # type: ignore[assignment]
    try:
        app = app_mod.create_app()
        app.state.db = object()  # a truthy handle; complete_signin is stubbed
        client = TestClient(app)
        resp = client.get("/auth/callback", follow_redirects=False)
    finally:
        app_mod._google_oauth = orig_oauth  # type: ignore[assignment]
        HS.complete_signin = orig_signin  # type: ignore[assignment]

    assert resp.status_code in (302, 307), resp.status_code
    set_cookies = resp.headers.get_list("set-cookie")
    session_cookies = [c for c in set_cookies if c.lower().startswith("session=")]
    assert len(session_cookies) == 1, (
        f"the callback must emit EXACTLY ONE 'session' cookie; got {session_cookies!r}"
    )
    # And the surviving cookie in the browser jar is the durable HMAC one.
    assert client.cookies.get("session") == durable, (
        "the browser must keep the durable HMAC session cookie resolve_session reads"
    )
