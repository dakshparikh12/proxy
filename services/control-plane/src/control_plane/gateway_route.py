"""The LIVE WS upgrade gateway mount + the per-connection dispatch drive (§4.3/§12.9).

This mounts ``/ws`` on the real control_plane app and does the TWO live-path jobs the
funnel needs to actually run in the running product:

1. **Auth at the UPGRADE** — it binds the framework-agnostic
   :func:`libs.http.authorize_upgrade` to the live session reader. An unauthenticated
   upgrade is rejected — the socket is CLOSED before it is ACCEPTED, so the 101 handshake
   never happens and no per-message dispatch ever runs on an unauthenticated connection.
2. **Driving the funnel per inbound frame** — once authenticated, the route runs the LIVE
   inbound message loop: every ``channel_action`` frame the client sends is fed through the
   ONE :func:`libs.http.dispatch` funnel (§4.3) against a repos-backed store (meeting→tenant
   resolved SERVER-SIDE from OUR ``meetings`` table) and the real inbound handler
   :func:`libs.http.handlers.channel_action.handle_channel_action`. dispatch() runs on the
   REAL product path — not isolation-only. The two generic error strings the funnel emits
   (``"Not found"`` / ``"Slow down."``) are written back over the socket; nothing else.

Auth is enforced at the connection upgrade, never per-message: the classic WS hole is a
socket that authenticates per-message. The tenant on the returned :class:`libs.http.Connection`
is resolved SERVER-SIDE from the signed session (the harness ``resolve_session`` over
``app.state.db``) — never a client-supplied field — which is exactly what the dispatch
funnel's isolation then relies on (§4.3).
"""
from __future__ import annotations

import os
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from starlette.websockets import WebSocket, WebSocketDisconnect

# The clean ``libs.http`` package seam — never the ``src`` deep path (the package boundary,
# §4.4). ``run_dispatch`` is the funnel coroutine re-exported under a shadow-proof name (the
# ``dispatch`` attribute collides with the same-named submodule; ``run_dispatch`` does not).
from libs.http import (
    DispatchCtx,
    RejectUpgrade,
    authorize_upgrade,
    handle_channel_action,
    run_dispatch,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

WS_PATH = "/ws"

# The per-connection inbound rate limit (§4.3) — the pinned `limits` moving window.
_WS_RATE_LIMIT = "60/minute"


def _allowed_origins() -> tuple[str, ...] | None:
    """The prod origin allowlist from ``PROXY_WS_ALLOWED_ORIGINS`` (comma-separated).

    Unset ⇒ ``None`` (no allowlist enforced — dev). Set ⇒ the upgrade is 403'd unless the
    ``Origin`` header is on the list. Never a client-supplied policy.
    """
    raw = os.environ.get("PROXY_WS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return None
    return tuple(o.strip() for o in raw.split(",") if o.strip())


class _MeetingStore:
    """The live repos-backed ownership oracle over OUR durable substrate (the funnel's Store).

    Resolves meeting→tenant SERVER-SIDE from OUR ``meetings`` table — the client's
    ``meeting_id`` is the only input, its claimed tenant is structurally never consulted
    (invariant 9, §4.3). ``entity_owning_meeting`` resolves a rendered entity (canvas/
    artifact) to its OWNING meeting: canvas/artifact surfaces are ephemeral in-meeting
    renders with no durable ownership row in the V0 substrate, so this returns ``None`` —
    the FAIL-CLOSED default, which makes the funnel refuse any smuggled ``canvas_id``
    generically (a durable entity table swaps in behind this same call when one exists).
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def meeting_tenant(self, meeting_id: UUID) -> Any | None:
        """Resolve ``meeting_id`` → its OWNING tenant from OUR ``meetings`` table (server-side)."""
        async with self._db.acquire() as conn:
            # meeting_id is already a UUID (the funnel validated it before this lookup),
            # so this is never a query on attacker-shaped input.
            return await conn.fetchval(
                "SELECT tenant_id FROM meetings WHERE id = $1", meeting_id
            )

    async def entity_owning_meeting(self, entity_id: UUID) -> UUID | None:
        """Resolve a rendered entity → its OWNING meeting from OUR store (fail-closed None)."""
        # No durable canvas/artifact ownership table in the V0 substrate — deny by
        # returning None so a smuggled entity id is refused generically upstream (§4.3).
        return None


def build_live_dispatch_ctx(db: Any) -> DispatchCtx:
    """Build the live :class:`libs.http.DispatchCtx` — repos-backed store + the real handler.

    Injects the repos-backed :class:`_MeetingStore` (meeting→tenant from OUR ``meetings``
    table) and the real inbound :func:`handle_channel_action`, with the pinned
    ``limits`` in-memory rate limiter (§11.11). This is what makes the funnel run on the
    REAL product path — the same ctx the tests build with a fake store, here bound to the
    live durable substrate. Built once per accepted connection off ``app.state.db``.
    """
    return DispatchCtx.build(
        store=_MeetingStore(db),
        handler=handle_channel_action,
        rate_limit=_WS_RATE_LIMIT,
    )


class _LiveConnection:
    """The authenticated live WS connection the funnel drives — writes errors to the socket.

    A structural :class:`libs.http.dispatch.Connection` (``id`` + ``tenant_id`` + async
    ``send_error``): it is NOT the frozen ``libs.http.Connection`` dataclass, because the
    funnel's ``Connection`` protocol expects settable attributes and the live socket must be
    held as mutable state. Carries the SERVER-SIDE-resolved ``tenant_id`` (from the signed
    session, never a client field) that the funnel checks every entity id against, and a
    per-connection ``id`` (the rate-limit key). :meth:`send_error` delivers ONLY the two
    generic funnel strings (``"Not found"`` / ``"Slow down."``) over the real socket.
    """

    def __init__(self, *, user_id: Any, tenant_id: Any, websocket: WebSocket) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.id = uuid4().hex  # the per-connection rate-limit key (§4.3)
        self._websocket = websocket

    async def send_error(self, message: str) -> None:
        """Write the funnel's generic error frame to the live socket (best-effort)."""
        try:
            await self._websocket.send_json({"error": message})
        except Exception:  # noqa: BLE001 — a closed socket must never crash the loop
            return None


def install_gateway_route(app: "FastAPI") -> None:
    """Mount the ``/ws`` gateway: 401 (reject) BEFORE the 101, then DRIVE the funnel per frame.

    Binds the real capability-gated handler into the contract registry (replacing the
    closure-closing stub — the registry stays closed, exactly-one-handler holds), resolves
    the signed session SERVER-SIDE off ``app.state.db`` at the upgrade (a missing session ⇒
    reject before the 101), and on success runs the LIVE inbound message loop that feeds
    every ``channel_action`` frame through :func:`libs.http.dispatch` (§4.3).
    """
    # Bind the REAL handler as THE live inbound handler for channel_action, swapping the
    # closure-closing default stub (§4.1). replace=True keeps exactly-one-handler — the
    # registry closure stays green; the funnel now routes to the capability-gated handler.
    from contracts.registry import MessageType, register_handler

    register_handler(MessageType.CHANNEL_ACTION, handle_channel_action, replace=True)

    @app.websocket(WS_PATH)
    async def ws_gateway(websocket: WebSocket) -> None:
        async def _resolve(cookies: dict[str, Any]) -> dict[str, Any] | None:
            db = getattr(websocket.app.state, "db", None)
            if db is None:
                return None  # no durable substrate handle → cannot authenticate → reject
            from control_plane.session import resolve_session

            resolved: dict[str, Any] | None = await resolve_session(db, cookies)
            return resolved

        try:
            authed = await authorize_upgrade(
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

        db = getattr(websocket.app.state, "db", None)
        if db is None:  # no substrate to isolate against → nothing safe to route
            await websocket.close(code=1011)
            return

        # The LIVE per-connection funnel drive: build the repos-backed ctx ONCE, then feed
        # every inbound channel_action frame through dispatch() (§4.3). The connection
        # carries the SERVER-SIDE tenant the funnel isolates against.
        ctx = build_live_dispatch_ctx(db)
        conn = _LiveConnection(
            user_id=authed.user_id, tenant_id=authed.tenant_id, websocket=websocket
        )
        try:
            while True:
                try:
                    raw = await websocket.receive_json()
                except JSONDecodeError:
                    # A non-JSON text frame is not a valid ProxyMessage — refuse generically
                    # and KEEP the loop alive; never crash the connection or leak a parse detail.
                    await conn.send_error("Not found")
                    continue
                if not isinstance(raw, dict):
                    # A JSON scalar/array is not a ProxyMessage object — same generic refusal.
                    await conn.send_error("Not found")
                    continue
                await run_dispatch(conn, raw, ctx)
        except WebSocketDisconnect:
            return  # the client hung up — end the loop cleanly


__all__ = [
    "WS_PATH",
    "build_live_dispatch_ctx",
    "install_gateway_route",
]
