"""Doc 08 · §4.3/§4.4 — the ONE dispatch funnel + §12.9 upgrade auth (gateway).

This node REBUILDS ``libs/http/dispatch.py`` as the ordered six-step funnel every
inbound WS ``channel_action`` flows through, and adds ``libs/http/gateway.py``'s
``authorize_upgrade`` (reject BEFORE the 101). The tests run the REAL funnel on the
REAL ``libs.contracts`` models — no mocks of the thing under test.

The funnel (§4.3), in order:

1. **rate-limit** per-connection via the pinned ``limits`` in-memory backend — NOT a
   hand-rolled bucket (CANONICAL §11.11). Over the window → generic ``"Slow down."``.
2. **registry lookup** by declared ``type`` — an unregistered type → generic ``"Not found"``.
3. **Pydantic-validate ONCE** centrally (§4.2) — a malformed/oversized body → ``"Not found"``.
4. **meeting/tenant isolation** keyed on ``meeting_id`` PRESENCE — resolved SERVER-SIDE
   against the authed tenant; absent-vs-foreign are the SAME generic ``"Not found"``.
5. **entity → owner → tenant** resolved from OUR store, NEVER the client ``meeting_id`` —
   a smuggled ``canvas_id`` owned by another meeting → ``"Not found"`` (the smuggle bug).
6. **route** to the EXACTLY-ONE handler — only reached once validated + isolated.

The core guarantees the DoD names:
* a foreign/absent ``meeting_id``, an unregistered type, an oversized/malformed body,
  or a smuggled ``canvas_id`` owned by another meeting is refused with a GENERIC error
  and NO handler runs;
* no error message distinguishes absent-vs-foreign tenant (that would leak tenancy);
* the client ``meeting_id`` is NEVER trusted to authorize an entity;
* isolation is by CONSTRUCTION — a handler never sees an un-isolated message.

Product imports live inside the test bodies so the module COLLECTS clean and fails RED
before the funnel is rebuilt.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

# ── the two tenants + their owned entities, the fixed world for every test ────
TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
A_MEETING = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")  # owned by tenant A
B_MEETING = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")  # owned by tenant B
A_CANVAS = UUID("a0a0a0a0-0000-0000-0000-000000000000")  # a canvas in A's meeting
B_CANVAS = UUID("b0b0b0b0-0000-0000-0000-000000000000")  # a canvas in B's meeting


class _FakeStore:
    """An in-memory stand-in for OUR durable store (the ``ctx.repos`` seam).

    It is the AUTHORITATIVE owner map: meeting→tenant and entity→owning-meeting. The
    funnel resolves ownership HERE (server-side), never trusting the client's payload.
    This is a stand-in for the substrate seam, NOT a mock of the funnel under test.
    """

    def __init__(self) -> None:
        self._meeting_tenant: dict[UUID, UUID] = {A_MEETING: TENANT_A, B_MEETING: TENANT_B}
        self._entity_meeting: dict[UUID, UUID] = {A_CANVAS: A_MEETING, B_CANVAS: B_MEETING}

    async def meeting_tenant(self, meeting_id: UUID) -> UUID | None:
        return self._meeting_tenant.get(meeting_id)

    async def entity_owning_meeting(self, entity_id: UUID) -> UUID | None:
        return self._entity_meeting.get(entity_id)


class _RecordingConn:
    """A stand-in ``Connection`` — carries the authed tenant + records send_error/routed."""

    def __init__(self, *, conn_id: str, tenant_id: UUID) -> None:
        self.id = conn_id
        self.tenant_id = tenant_id
        self.errors: list[str] = []

    async def send_error(self, message: str) -> None:
        self.errors.append(message)


def _make_ctx(store: _FakeStore, *, handler_calls: list, limit: str = "100/minute"):
    """Build a real DispatchCtx: the pinned limits rate-limiter + the store + the one handler."""
    from libs.http.dispatch import DispatchCtx

    async def _handler(conn, msg, ctx) -> None:
        handler_calls.append((conn, msg))

    return DispatchCtx.build(store=store, handler=_handler, rate_limit=limit)


# ── step 6 (happy path) — validated + isolated + owned → EXACTLY ONE handler ───
@pytest.mark.asyncio
async def test_well_formed_owned_message_routes_to_exactly_one_handler():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {
        "type": "channel_action",
        "meeting_id": str(A_MEETING),
        "surface": "voice",
        "action": "catch_me_up",
    }
    await dispatch(conn, raw, ctx)

    assert conn.errors == [], f"a well-formed owned message must NOT error: {conn.errors}"
    assert len(calls) == 1, "exactly one handler runs for a validated+isolated message"
    _routed_conn, routed_msg = calls[0]
    assert routed_msg.meeting_id == A_MEETING
    # The handler receives the VALIDATED model, never the raw dict.
    assert not isinstance(routed_msg, dict)


# ── step 4 — foreign meeting_id: refused, generic, NO handler ─────────────────
@pytest.mark.asyncio
async def test_foreign_meeting_id_is_refused_generically_and_routes_nothing():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    # Tenant B connection tries to act on tenant A's meeting (server-side tenant mismatch).
    conn = _RecordingConn(conn_id="c-B", tenant_id=TENANT_B)

    raw = {
        "type": "channel_action",
        "meeting_id": str(A_MEETING),  # NOT owned by tenant B
        "surface": "voice",
        "action": "catch_me_up",
    }
    await dispatch(conn, raw, ctx)

    assert calls == [], "a foreign meeting_id must route NOTHING"
    assert conn.errors == ["Not found"], f"foreign tenant must get the generic error: {conn.errors}"


# ── step 4 — absent-vs-foreign are the SAME generic error (no tenancy leak) ────
@pytest.mark.asyncio
async def test_absent_and_foreign_meeting_produce_the_identical_generic_error():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []

    # foreign: tenant B on tenant A's real meeting
    ctx1 = _make_ctx(store, handler_calls=calls)
    conn_foreign = _RecordingConn(conn_id="c-B1", tenant_id=TENANT_B)
    await dispatch(
        conn_foreign,
        {"type": "channel_action", "meeting_id": str(A_MEETING), "surface": "voice", "action": "catch_me_up"},
        ctx1,
    )

    # absent: a meeting_id that does not exist in OUR store at all
    ctx2 = _make_ctx(store, handler_calls=calls)
    conn_absent = _RecordingConn(conn_id="c-B2", tenant_id=TENANT_B)
    await dispatch(
        conn_absent,
        {"type": "channel_action", "meeting_id": str(uuid4()), "surface": "voice", "action": "catch_me_up"},
        ctx2,
    )

    assert calls == [], "neither foreign nor absent may route"
    assert conn_foreign.errors == conn_absent.errors == ["Not found"], (
        "absent and foreign MUST be byte-identical — a distinguishable error leaks tenancy"
    )


# ── step 5 — smuggled canvas_id owned by ANOTHER meeting → refused, no handler ─
@pytest.mark.asyncio
async def test_smuggled_foreign_canvas_id_is_refused_even_with_own_meeting_id():
    """The smuggle-a-foreign-id bug: {my meeting_id, victim's canvas_id}.

    Tenant B authenticates, sends its OWN meeting_id (which it owns) but smuggles a
    ``canvas_id`` that belongs to tenant A's meeting. Trusting the client meeting_id
    would authorize the entity — the funnel MUST resolve the canvas's OWNING meeting
    from OUR store and tenant-check THAT, refusing generically.
    """
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-B", tenant_id=TENANT_B)

    raw = {
        "type": "channel_action",
        "meeting_id": str(B_MEETING),  # tenant B DOES own this — the meeting check passes
        "surface": "canvas",
        "action": "walkthrough_on",
        "canvas_id": str(A_CANVAS),  # but the canvas belongs to tenant A's meeting
    }
    await dispatch(conn, raw, ctx)

    assert calls == [], "a smuggled foreign entity id must route NOTHING"
    assert conn.errors == ["Not found"], f"the smuggle must be refused generically: {conn.errors}"


# ── step 5 — a canvas the tenant DOES own passes both checks and routes ────────
@pytest.mark.asyncio
async def test_owned_canvas_id_passes_entity_isolation_and_routes():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {
        "type": "channel_action",
        "meeting_id": str(A_MEETING),
        "surface": "canvas",
        "action": "walkthrough_on",
        "canvas_id": str(A_CANVAS),  # A's own canvas in A's own meeting
    }
    await dispatch(conn, raw, ctx)

    assert conn.errors == [], f"an owned canvas must not error: {conn.errors}"
    assert len(calls) == 1, "a validated+isolated owned-canvas message routes exactly once"


# ── step 2 — an unregistered type → generic "Not found", no type leak ─────────
@pytest.mark.asyncio
async def test_unregistered_type_is_refused_generically_without_type_leak():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {"type": "totally.bogus.type", "meeting_id": str(A_MEETING)}
    await dispatch(conn, raw, ctx)

    assert calls == [], "an unregistered type routes nothing"
    assert conn.errors == ["Not found"], f"unregistered type → generic error: {conn.errors}"
    # The error must NEVER echo the attacker-controlled type string.
    assert "totally.bogus.type" not in "".join(conn.errors)


# ── step 3 — an oversized arg (over the model's Field(max_length)) → "Not found" ─
@pytest.mark.asyncio
async def test_oversized_body_is_refused_generically():
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {
        "type": "channel_action",
        "meeting_id": str(A_MEETING),
        "surface": "voice",
        "action": "catch_me_up",
        "arg": "x" * 2_001,  # over ChannelAction.arg's Field(max_length=2000)
    }
    await dispatch(conn, raw, ctx)

    assert calls == [], "an oversized body routes nothing"
    assert conn.errors == ["Not found"], f"oversized body → generic error: {conn.errors}"


# ── step 3 — a malformed body (non-UUID meeting_id) → refused BEFORE any lookup ─
@pytest.mark.asyncio
async def test_malformed_meeting_id_is_rejected_before_any_db_lookup():
    """A non-UUID meeting_id is a ValidationError in step 3 — BEFORE step 4's lookup.

    This is what makes isolation SOUND: the funnel never runs a store query on
    attacker-shaped input. We prove it by handing a store that RAISES if queried.
    """
    from libs.http.dispatch import DispatchCtx, dispatch

    class _ExplodingStore:
        async def meeting_tenant(self, meeting_id):  # noqa: ANN001
            raise AssertionError("store queried with attacker-shaped input — isolation UNSOUND")

        async def entity_owning_meeting(self, entity_id):  # noqa: ANN001
            raise AssertionError("store queried with attacker-shaped input — isolation UNSOUND")

    calls: list = []

    async def _handler(conn, msg, ctx) -> None:  # noqa: ANN001
        calls.append(msg)

    ctx = DispatchCtx.build(store=_ExplodingStore(), handler=_handler, rate_limit="100/minute")
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {"type": "channel_action", "meeting_id": "not-a-uuid", "surface": "voice", "action": "catch_me_up"}
    await dispatch(conn, raw, ctx)  # must NOT raise from the store

    assert calls == [], "a malformed body routes nothing"
    assert conn.errors == ["Not found"], f"malformed body → generic error: {conn.errors}"


# ── step 1 — rate-limit is the pinned `limits` in-memory backend, per connection ─
@pytest.mark.asyncio
async def test_rate_limit_uses_pinned_limits_backend_keyed_per_connection():
    """The limiter is `limits`' MovingWindow/FixedWindow over MemoryStorage — NOT hand-rolled.

    Over the window, the SAME connection is refused with the generic "Slow down."; a
    DIFFERENT connection (a different key) is unaffected — proving per-connection keying.
    """
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls, limit="2/minute")  # tiny window to trip it
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {"type": "channel_action", "meeting_id": str(A_MEETING), "surface": "voice", "action": "catch_me_up"}

    # first two are within the window → route
    await dispatch(conn, dict(raw), ctx)
    await dispatch(conn, dict(raw), ctx)
    assert len(calls) == 2, "the first two are within the window"
    assert conn.errors == []

    # third trips the window → generic "Slow down.", NO handler
    await dispatch(conn, dict(raw), ctx)
    assert len(calls) == 2, "the throttled message must not route"
    assert conn.errors == ["Slow down."], f"over-window → generic slow-down: {conn.errors}"

    # a DIFFERENT connection is a DIFFERENT key → unaffected (per-connection keying)
    conn2 = _RecordingConn(conn_id="c-A2", tenant_id=TENANT_A)
    await dispatch(conn2, dict(raw), ctx)
    assert len(calls) == 3, "a different connection is a different rate-limit key"
    assert conn2.errors == []


@pytest.mark.asyncio
async def test_rate_limiter_is_the_limits_library_not_hand_rolled():
    """The limiter object is a real `limits` strategy over `limits` MemoryStorage.

    A hand-rolled token bucket would fail this structural check — the DoD forbids it.
    """
    import limits.strategies as _strat
    from limits.storage import MemoryStorage

    from libs.http.dispatch import DispatchCtx

    store = _FakeStore()

    async def _handler(conn, msg, ctx) -> None:  # noqa: ANN001
        return None

    ctx = DispatchCtx.build(store=store, handler=_handler, rate_limit="100/minute")
    limiter = ctx.rate_limiter
    # The strategy is one of limits' window strategies, over limits' in-memory storage.
    assert isinstance(limiter.strategy, _strat.RateLimiter), "must be a limits strategy"
    assert isinstance(limiter.strategy.storage, MemoryStorage), "must be the limits in-memory backend"


# ── the is_owner fence (§12.10) is FOLDED into the funnel, not dropped ────────
@pytest.mark.asyncio
async def test_reclaimed_process_is_owner_false_refuses_and_routes_nothing():
    """A reclaimed process (is_owner False) must not route a side-effecting message (§12.10)."""
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    calls: list = []
    ctx = _make_ctx(store, handler_calls=calls)
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)

    raw = {"type": "channel_action", "meeting_id": str(A_MEETING), "surface": "voice", "action": "catch_me_up"}
    await dispatch(conn, raw, ctx, is_owner=False)

    assert calls == [], "a reclaimed (is_owner False) process routes nothing"


# ── isolation is by CONSTRUCTION: the handler NEVER sees an un-isolated message ─
@pytest.mark.asyncio
async def test_isolation_is_by_construction_not_opt_in_per_handler():
    """Whatever reaches the handler is ALREADY tenant-verified — every entity id was
    authorized server-side against the authed tenant. We assert the handler only ever
    fires for a message whose meeting_id resolves to the connection's own tenant.
    """
    from libs.http.dispatch import dispatch

    store = _FakeStore()
    seen: list = []
    ctx = _make_ctx(store, handler_calls=seen)

    # A run of mixed messages from a tenant-A connection: only the OWNED one may route.
    conn = _RecordingConn(conn_id="c-A", tenant_id=TENANT_A)
    messages = [
        {"type": "channel_action", "meeting_id": str(A_MEETING), "surface": "voice", "action": "catch_me_up"},  # own
        {"type": "channel_action", "meeting_id": str(B_MEETING), "surface": "voice", "action": "catch_me_up"},  # foreign
        {"type": "channel_action", "meeting_id": str(A_MEETING), "surface": "canvas",
         "action": "walkthrough_on", "canvas_id": str(B_CANVAS)},  # smuggled foreign canvas
    ]
    for raw in messages:
        await dispatch(conn, raw, ctx)

    assert len(seen) == 1, "only the fully-owned message may reach the handler"
    _c, msg = seen[0]
    resolved_tenant = await store.meeting_tenant(msg.meeting_id)
    assert resolved_tenant == conn.tenant_id, "the handler only ever sees own-tenant messages"


# ── the exposed server-side entity->tenant resolver (AC-TEN-002 seam) ─────────
@pytest.mark.asyncio
async def test_resolve_entity_tenant_refuses_cross_tenant_read():
    """``libs.http.resolve_entity_tenant`` is the server-side resolver AC-TEN-002 imports.

    A tenant-B principal resolving a tenant-A meeting is DENIED and no tenant-A data
    is handed back (never the client's claimed tenant).
    """
    from libs.http import resolve_entity_tenant

    store = _FakeStore()
    principal_b = {"user_id": "b0b0b0b0-0000-0000-0000-000000000000", "tenant_id": str(TENANT_B)}
    outcome = await resolve_entity_tenant(
        entity_id=str(A_MEETING), entity_type="meeting", principal=principal_b, store=store
    )
    # Denied: not resolved into tenant A's scope.
    if isinstance(outcome, dict):
        assert outcome.get("tenant_id") != str(TENANT_A)
        assert outcome.get("allowed") is False or outcome.get("denied") is True
    else:
        assert outcome in (None, False, [])
