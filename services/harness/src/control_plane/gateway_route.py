"""The LIVE WS upgrade gateway mount — auth BEFORE the 101 (§4.3/§12.9).

This mounts ``/ws`` on the real control_plane app and binds the framework-agnostic
:func:`libs.http.authorize_upgrade` to the live session reader. An unauthenticated
upgrade is rejected — the socket is CLOSED before it is ACCEPTED, so the 101 handshake
never happens and no per-message dispatch ever runs on an unauthenticated connection.

Auth is enforced HERE, at the connection upgrade, never per-message: the classic WS hole
is a socket that authenticates per-message. The tenant on the returned
:class:`libs.http.Connection` is resolved SERVER-SIDE from the signed session (the
harness ``resolve_session`` over ``app.state.db``) — never a client-supplied field — which
is exactly what the dispatch funnel's isolation then relies on (§4.3).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from libs.http.src.http.gateway import RejectUpgrade, authorize_upgrade

if TYPE_CHECKING:
    from fastapi import FastAPI

WS_PATH = "/ws"


def _allowed_origins() -> tuple[str, ...] | None:
    """The prod origin allowlist from ``PROXY_WS_ALLOWED_ORIGINS`` (comma-separated).

    Unset ⇒ ``None`` (no allowlist enforced — dev). Set ⇒ the upgrade is 403'd unless the
    ``Origin`` header is on the list. Never a client-supplied policy.
    """
    raw = os.environ.get("PROXY_WS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return None
    return tuple(o.strip() for o in raw.split(",") if o.strip())


def install_gateway_route(app: "FastAPI") -> None:
    """Mount the ``/ws`` WS upgrade gateway: 401 (reject) BEFORE the 101 (accept).

    The endpoint resolves the signed session SERVER-SIDE off ``app.state.db``; a missing
    session (or a missing durable handle) rejects the upgrade by CLOSING the socket before
    accepting it — the 101 never fires. On success it accepts and holds the authenticated
    :class:`libs.http.Connection`; the per-message dispatch funnel runs only from here on.
    """
    from starlette.websockets import WebSocket

    @app.websocket(WS_PATH)
    async def ws_gateway(websocket: WebSocket) -> None:
        async def _resolve(cookies: dict[str, Any]) -> dict[str, Any] | None:
            db = getattr(websocket.app.state, "db", None)
            if db is None:
                return None  # no durable substrate handle → cannot authenticate → reject
            from harness.session import resolve_session

            resolved: dict[str, Any] | None = await resolve_session(db, cookies)
            return resolved

        try:
            await authorize_upgrade(
                websocket,
                resolve_session=_resolve,
                allowed_origins=_allowed_origins(),
            )
        except RejectUpgrade as rej:
            # Reject BEFORE the 101: close the handshake without ever accepting it. The
            # WS close code carries the auth failure; the socket never opened.
            await websocket.close(code=1008, reason=str(rej.status))
            return

        # Authenticated: NOW the 101 handshake completes. From here the per-connection
        # dispatch funnel (§4.3) owns every inbound message; auth is never re-checked
        # per-message.
        await websocket.accept()
        await websocket.close(code=1000)  # V0 mount point; the live message loop attaches here.


__all__ = ["WS_PATH", "install_gateway_route"]
