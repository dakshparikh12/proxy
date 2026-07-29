"""Doc 08 · §4.3/§4.4 — the funnel is DRIVEN on the LIVE product path (confirm b).

The funnel LOGIC (``libs.http.dispatch.dispatch``) and its one handler
(``libs.http.handlers.channel_action.handle_channel_action``) are already built and
unit-bound. This node proves the missing half the verifier flagged: the funnel is
actually WIRED into the running product, not isolation-only.

Three live-path facts, each on the REAL objects (no mock of the thing under test):

1. **The real handler is the registered live handler** — after the control_plane app is
   built, ``MESSAGE_HANDLERS['channel_action']`` is the capability-gated
   ``handle_channel_action``, NOT the closure-closing stub ``_default_channel_action_handler``.
2. **The live /ws route runs an inbound message loop** — an authenticated socket receives a
   ``channel_action`` frame and the funnel runs on it (it is NOT accepted-then-immediately-
   closed). A foreign/unknown meeting_id is refused with the GENERIC error over the wire
   (proving dispatch() ran server-side isolation on the real path).
3. **The live route builds a repos-backed DispatchCtx** — ``build_live_dispatch_ctx`` resolves
   meeting→tenant SERVER-SIDE from OUR ``meetings`` table (never a client field), and the
   registry stays closed after the live handler is bound (exactly-one-handler holds).

Product imports live inside the test bodies so the module COLLECTS clean and fails RED
before the live wiring exists.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest


# ── 1 · the LIVE registered handler is the real capability-gated one, not the stub ──
def test_live_channel_action_handler_is_the_real_handler_not_the_stub():
    """Building the control_plane app binds the real handler into MESSAGE_HANDLERS."""
    from contracts.registry import MESSAGE_HANDLERS, MessageType

    # Building the live app is what wires the live handler (import side-effect free of it).
    from services.control_plane import create_app

    create_app()

    # Assert via the SAME canonical package seam the live mount binds through
    # (``libs.http`` — not the ``src`` deep path), so this is one module object, one identity.
    from libs.http import handle_channel_action

    bound = MESSAGE_HANDLERS[MessageType.CHANNEL_ACTION]
    assert bound is handle_channel_action, (
        "the LIVE MESSAGE_HANDLERS['channel_action'] must be the real capability-gated "
        f"handle_channel_action, not the stub — got {bound!r}"
    )
    # It must NOT be the closure-closing default stub any longer.
    from contracts.registry import _default_channel_action_handler

    assert bound is not _default_channel_action_handler, (
        "the real path must REPLACE the stub handler, not leave it in place"
    )


def test_registry_stays_closed_after_the_live_handler_is_bound():
    """Rebinding the live handler keeps the contract graph closed (exactly-one-handler)."""
    from contracts.registry import assert_registry_closed
    from services.control_plane import create_app

    create_app()
    assert_registry_closed()  # raises AssertionError on any drift — exactly one handler holds


# ── 2 · the live route resolves meeting→tenant SERVER-SIDE from OUR store ──────────
@pytest.mark.asyncio
async def test_build_live_dispatch_ctx_resolves_meeting_tenant_from_our_meetings_table():
    """The live DispatchCtx's store reads meeting→tenant from OUR ``meetings`` table.

    We hand a fake DB whose ``meetings`` row maps a meeting to tenant-A; the built store
    resolves THAT tenant. A client-supplied tenant is structurally never consulted — the
    store's only input is the meeting_id and OUR durable substrate.
    """
    from services.control_plane.gateway_route import build_live_dispatch_ctx

    tenant_a = uuid4()
    meeting_a = uuid4()

    class _FakeConn:
        async def fetchval(self, sql: str, *args: Any) -> Any:
            # Emulate: SELECT tenant_id FROM meetings WHERE id = $1
            if "meetings" in sql and args and args[0] == meeting_a:
                return tenant_a
            return None

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _FakeDB:
        def acquire(self) -> _FakeAcquire:
            return _FakeAcquire()

    ctx = build_live_dispatch_ctx(_FakeDB())

    # meeting→tenant resolves SERVER-SIDE from our table.
    resolved = await ctx.store.meeting_tenant(meeting_a)
    assert str(resolved) == str(tenant_a), "the live store must read meeting→tenant from OUR meetings table"
    # an absent meeting resolves to None (fail-closed: absent == foreign generic refusal upstream).
    assert await ctx.store.meeting_tenant(uuid4()) is None
    # the live ctx routes to the REAL handler (via the canonical package seam).
    from libs.http import handle_channel_action

    assert ctx.handler is handle_channel_action


# ── 3 · the live /ws route DRIVES dispatch() per received frame (not accept-then-close) ──
def test_live_ws_route_drives_the_funnel_on_an_authenticated_frame():
    """An authenticated socket that sends a channel_action gets the funnel's GENERIC refusal.

    We stub the session resolver (auth) and the durable handle so the upgrade is accepted,
    then send a channel_action for a meeting OUR store does not own → the funnel must run
    server-side isolation and reply with the generic ``"Not found"`` over the wire. The old
    accept-then-immediately-close mount would send NO such frame (the loop never ran).
    """
    from starlette.testclient import TestClient

    from services.control_plane import create_app

    app = create_app()

    tenant_a = str(uuid4())

    # Bind a durable handle whose session resolves (auth passes) and whose meetings table
    # owns NOTHING for the sent meeting_id (so the funnel refuses generically).
    class _FakeConn:
        async def fetchval(self, sql: str, *args: Any) -> Any:
            return None  # no meeting owned → foreign/absent → generic refusal

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _FakeDB:
        def acquire(self) -> _FakeAcquire:
            return _FakeAcquire()

    app.state.db = _FakeDB()

    # Make the session resolver authenticate (server-side tenant), so the upgrade opens.
    async def _fake_resolve_session(db: Any, cookies: dict[str, Any]) -> dict[str, Any] | None:
        return {"user_id": str(uuid4()), "tenant_id": tenant_a}

    import control_plane.session as sess

    orig = sess.resolve_session
    sess.resolve_session = _fake_resolve_session  # type: ignore[assignment]
    try:
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "type": "channel_action",
                    "meeting_id": str(uuid4()),  # not owned by our store → foreign
                    "surface": "voice",
                    "action": "catch_me_up",
                }
            )
            reply = ws.receive_json()
        # The funnel RAN and refused generically over the wire — the loop drove dispatch().
        assert reply.get("error") == "Not found", (
            f"the live funnel must reply with the generic error over the wire; got {reply!r}"
        )
    finally:
        sess.resolve_session = orig  # type: ignore[assignment]


def test_live_ws_route_routes_an_owned_frame_through_to_the_capability_gated_handler():
    """An authenticated frame for an OWNED meeting flows the whole funnel → the real handler.

    OUR meetings table owns the sent meeting for the connection's server-side tenant, so the
    funnel's isolation passes and it routes to ``handle_channel_action``. The capability-gated
    handler is a no-op success when no live service is bound (§4.4), so NO error frame is sent —
    proving dispatch() ran end-to-end and reached the real handler (not accept-then-close).
    """
    from starlette.testclient import TestClient

    from services.control_plane import create_app

    app = create_app()

    tenant_a = str(uuid4())
    owned_meeting = uuid4()

    class _FakeConn:
        async def fetchval(self, sql: str, *args: Any) -> Any:
            # OUR meetings table: the owned meeting resolves to the connection's tenant.
            if "meetings" in sql and args and args[0] == owned_meeting:
                return tenant_a
            return None

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _FakeDB:
        def acquire(self) -> _FakeAcquire:
            return _FakeAcquire()

    app.state.db = _FakeDB()

    async def _fake_resolve_session(db: Any, cookies: dict[str, Any]) -> dict[str, Any] | None:
        return {"user_id": str(uuid4()), "tenant_id": tenant_a}

    import control_plane.session as sess

    orig = sess.resolve_session
    sess.resolve_session = _fake_resolve_session  # type: ignore[assignment]
    try:
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "type": "channel_action",
                    "meeting_id": str(owned_meeting),  # OWNED by our store for this tenant
                    "surface": "voice",
                    "action": "catch_me_up",
                }
            )
            # An owned frame routes to the no-op handler → NO error frame. Then send a foreign
            # frame and assert IT is refused — proving the loop is still alive AND routing.
            ws.send_json(
                {
                    "type": "channel_action",
                    "meeting_id": str(uuid4()),  # foreign → generic refusal
                    "surface": "voice",
                    "action": "catch_me_up",
                }
            )
            reply = ws.receive_json()
        assert reply.get("error") == "Not found", (
            "the owned frame must route silently (no error); the next foreign frame proves the "
            f"loop kept running and dispatch() drove both — got {reply!r}"
        )
    finally:
        sess.resolve_session = orig  # type: ignore[assignment]


def test_live_ws_route_refuses_a_malformed_frame_generically_and_keeps_the_loop_alive():
    """A non-JSON / non-object frame is refused with the generic error; the loop never crashes.

    Fail-closed (laws 1/2 + the never-throw discipline): a garbage text frame or a JSON scalar
    is not a valid ProxyMessage, so the funnel-drive answers ``"Not found"`` and keeps serving —
    a single bad frame must not tear down the authenticated connection or leak a parse detail.
    """
    from starlette.testclient import TestClient

    from services.control_plane import create_app

    app = create_app()
    tenant_a = str(uuid4())

    class _FakeConn:
        async def fetchval(self, sql: str, *args: Any) -> Any:
            return None

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _FakeDB:
        def acquire(self) -> _FakeAcquire:
            return _FakeAcquire()

    app.state.db = _FakeDB()

    async def _fake_resolve_session(db: Any, cookies: dict[str, Any]) -> dict[str, Any] | None:
        return {"user_id": str(uuid4()), "tenant_id": tenant_a}

    import control_plane.session as sess

    orig = sess.resolve_session
    sess.resolve_session = _fake_resolve_session  # type: ignore[assignment]
    try:
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("this is not json at all {{{")  # a non-JSON text frame
            first = ws.receive_json()
            assert first.get("error") == "Not found", f"garbage frame → generic refusal; got {first!r}"
            # The loop is STILL alive: a JSON scalar (not an object) is also refused generically.
            ws.send_json(42)
            second = ws.receive_json()
            assert second.get("error") == "Not found", (
                f"the loop survived the garbage frame and refused the scalar too; got {second!r}"
            )
    finally:
        sess.resolve_session = orig  # type: ignore[assignment]
