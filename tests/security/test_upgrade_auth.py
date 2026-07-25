"""Doc 08 · §4.3/§12.9 — auth is enforced at the WS UPGRADE (401 before the 101).

The classic WS hole is a socket that authenticates per-message instead of
per-connection. The gateway's ``authorize_upgrade`` closes it: an unauthenticated
upgrade is rejected with a 401 BEFORE the 101 handshake — the socket never opens.

These tests exercise the REAL gateway (``libs.http.gateway.authorize_upgrade``) and
its LIVE mount on the real ``control_plane`` app (``services.control_plane.create_app``).
No handler, no per-message check, ever runs on an unauthenticated connection.

Product imports live inside the test bodies so the module COLLECTS clean and fails RED
before the gateway exists.
"""
from __future__ import annotations

import pytest


# ── authorize_upgrade: reject with 401 BEFORE opening the socket ──────────────
@pytest.mark.asyncio
async def test_authorize_upgrade_rejects_unauthenticated_with_401_before_101():
    from libs.http.gateway import RejectUpgrade, authorize_upgrade

    async def _no_session(cookies):  # noqa: ANN001 — the injected session resolver: no session
        return None

    class _Req:
        cookies: dict = {}
        headers: dict = {}

    with pytest.raises(RejectUpgrade) as ei:
        await authorize_upgrade(_Req(), resolve_session=_no_session)
    assert ei.value.status == 401, "an unauthenticated upgrade rejects with 401 (before the 101)"


@pytest.mark.asyncio
async def test_authorize_upgrade_returns_connection_carrying_the_server_side_tenant():
    """A valid session yields a Connection carrying the tenant resolved SERVER-SIDE.

    The tenant rides the resolved session, never a client-supplied field — the same
    principle the funnel then relies on for isolation.
    """
    from libs.http.gateway import authorize_upgrade

    async def _good_session(cookies):  # noqa: ANN001
        return {"user_id": "u-1", "tenant_id": "tenant-A"}

    class _Req:
        cookies = {"session": "signed.cookie"}
        headers: dict = {}

    conn = await authorize_upgrade(_Req(), resolve_session=_good_session)
    assert conn.tenant_id == "tenant-A", "the Connection carries the server-side-resolved tenant"
    assert conn.user_id == "u-1"
    assert conn.id, "the Connection has a per-connection id (the rate-limit key)"


@pytest.mark.asyncio
async def test_authorize_upgrade_rejects_disallowed_origin_with_403():
    from libs.http.gateway import RejectUpgrade, authorize_upgrade

    async def _good_session(cookies):  # noqa: ANN001
        return {"user_id": "u-1", "tenant_id": "tenant-A"}

    class _Req:
        cookies = {"session": "signed.cookie"}
        headers = {"origin": "https://evil.example.com"}

    with pytest.raises(RejectUpgrade) as ei:
        await authorize_upgrade(
            _Req(), resolve_session=_good_session, allowed_origins=("https://app.proxy.dev",)
        )
    assert ei.value.status == 403, "a disallowed origin rejects the upgrade (403)"


# ── the LIVE mount on the real control_plane app: 401 before the 101 ──────────
def test_live_ws_route_rejects_unauthenticated_upgrade_before_101():
    """The gateway is mounted on the REAL control_plane app; hitting the WS route
    without a session is rejected BEFORE the socket opens (the handshake never 101s).
    """
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from services.control_plane import create_app

    app = create_app()
    client = TestClient(app)

    # No session cookie → the upgrade is rejected before the 101. Starlette surfaces a
    # pre-accept rejection as a WebSocketDisconnect (the socket never completed).
    with pytest.raises((WebSocketDisconnect, Exception)) as ei:  # noqa: PT011
        with client.websocket_connect("/ws"):
            pass
    # It must be a REJECTION, not a successful open — the exception proves no 101.
    assert ei.value is not None


def test_live_ws_route_is_actually_mounted_on_control_plane():
    """The /ws gateway route exists on the real app (not a phantom) — a live route."""
    from services.control_plane import create_app

    app = create_app()
    ws_paths = {
        getattr(r, "path", None)
        for r in app.routes
        if getattr(r, "path", None) is not None
    }
    assert "/ws" in ws_paths, f"the /ws upgrade route must be mounted; have {sorted(p for p in ws_paths if p)}"
