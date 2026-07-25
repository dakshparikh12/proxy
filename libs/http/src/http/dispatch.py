"""The ONE dispatch funnel — every inbound WS ``channel_action`` flows through here (§4.3).

This REBUILDS the old three-line stub into the ordered six-step funnel of Doc 08 §4.3.
Isolation is by **construction**, not opt-in per handler: a handler never sees an
un-isolated message, because every entity id on the message was authorized SERVER-SIDE
against the connection's authed tenant before a single line of handler logic ran. Every
failure returns a **generic** error (``"Not found"`` / ``"Slow down."``) — never a
type/tenancy leak; absent and foreign are the SAME error, so the error can't be an oracle.

The six ordered steps:

1. **rate-limit** per-connection (``conn.id``) via the pinned ``limits`` in-memory backend
   (CANONICAL §11.11) — a moving-window strategy over ``limits.storage.MemoryStorage``, NOT
   a hand-rolled token bucket. Over the window ⇒ generic ``"Slow down."``.
2. **registry lookup** by the declared ``type`` (``CHANNEL_REGISTRY``) — an unregistered
   type ⇒ generic ``"Not found"`` (never "unknown type X" — no info leak).
3. **Pydantic-validate ONCE**, centrally (§4.2) — a malformed/oversized body (including a
   non-UUID ``meeting_id``, rejected BEFORE any store lookup) ⇒ generic ``"Not found"``.
4. **meeting/tenant isolation** keyed on ``meeting_id`` PRESENCE — resolved SERVER-SIDE
   against ``conn.tenant_id``; absent-vs-foreign yield the identical generic ``"Not found"``.
   A scoped message with no ``meeting_id`` hits the default-reject floor.
5. **entity → owner → tenant** resolved from OUR store, NEVER the client ``meeting_id`` — a
   smuggled ``canvas_id`` owned by another meeting is refused (kills the smuggle bug).
6. **route** to the EXACTLY-ONE handler — reached only once validated + isolated.

The §12.10 ``is_owner`` fence is FOLDED IN (not dropped): a reclaimed process
(``is_owner=False``) routes nothing — it must never emit a side-effecting message.

The store and rate-limit policy are INJECTED via :class:`DispatchCtx` so ``libs/http``
carries no hard dependency on the ``db`` plumbing; the live control_plane mount binds the
real repos, the tests bind a fake store. The funnel owns the ORDER and the fail-closed
generic rejection — the isolation guarantee that makes an unchecked route impossible.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from contracts.registry import CHANNEL_REGISTRY
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter, RateLimiter

# The two — and only two — generic error strings the funnel ever sends to a client.
# Both are intentionally uninformative: a distinguishable message would leak whether an
# id was absent vs. owned-by-another-tenant (a tenancy oracle) or echo an attacker type.
_ERR_NOT_FOUND = "Not found"
_ERR_SLOW_DOWN = "Slow down."


class Connection(Protocol):
    """The authenticated WS connection the funnel runs against (from the gateway §12.9).

    ``tenant_id`` was resolved SERVER-SIDE at the upgrade — never a client-supplied field;
    the funnel checks every entity id against it. ``id`` is the per-connection rate-limit
    key. ``send_error`` delivers ONLY the two generic strings above.
    """

    id: str
    tenant_id: Any

    async def send_error(self, message: str) -> None: ...


class Store(Protocol):
    """The server-side ownership oracle (a seam over OUR durable substrate).

    ``meeting_tenant`` resolves a meeting to its OWNING tenant; ``entity_owning_meeting``
    resolves an entity (canvas/artifact) to its OWNING meeting. BOTH read our store — the
    client's ``meeting_id`` is never trusted to authorize an entity (the smuggle fix, §4.3).
    """

    async def meeting_tenant(self, meeting_id: UUID) -> Any | None: ...

    async def entity_owning_meeting(self, entity_id: UUID) -> UUID | None: ...


# The one inbound handler signature: (conn, validated_msg, ctx) -> awaitable None.
Handler = Callable[["Connection", Any, "DispatchCtx"], Awaitable[None]]


@dataclass(frozen=True)
class PerConnectionRateLimiter:
    """The pinned rate limiter — ``limits`` moving-window strategy over its in-memory store.

    This is the CANONICAL §11.11 backend, NOT a hand-rolled token bucket: ``strategy`` is a
    real :class:`limits.strategies.RateLimiter` and ``storage`` is
    :class:`limits.storage.MemoryStorage`. ``check(key)`` records one hit keyed per
    connection (``conn.id``) and returns ``True`` while within the window, ``False`` over it.
    A distributed (Redis) backend is an Expansion swap behind this same call.
    """

    strategy: RateLimiter
    item: RateLimitItem

    def check(self, key: str) -> bool:
        """Record a hit for ``key`` (the connection id); ``True`` if within the window."""
        return bool(self.strategy.hit(self.item, key))

    @property
    def storage(self) -> Any:
        """The underlying ``limits`` storage backend (in-memory for V0)."""
        return self.strategy.storage


@dataclass(frozen=True)
class DispatchCtx:
    """The funnel's dependencies — the rate limiter, the store, and the one handler.

    Built once per host via :meth:`build` (which constructs the pinned ``limits`` limiter);
    the live control_plane mount injects the real repos-backed store, the tests inject a
    fake store. The funnel reads ONLY these three seams.
    """

    rate_limiter: PerConnectionRateLimiter
    store: Store
    handler: Handler

    @classmethod
    def build(
        cls,
        *,
        store: Store,
        handler: Handler,
        rate_limit: str = "60/minute",
    ) -> DispatchCtx:
        """Construct a ctx with the pinned ``limits`` in-memory limiter parsed from ``rate_limit``.

        ``rate_limit`` is a ``limits`` rate string (e.g. ``"60/minute"``, ``"2/second"``).
        The strategy is a moving window over ``MemoryStorage`` — the §11.11 pin.
        """
        limiter = PerConnectionRateLimiter(
            strategy=MovingWindowRateLimiter(MemoryStorage()),
            item=parse(rate_limit),
        )
        return cls(rate_limiter=limiter, store=store, handler=handler)


async def dispatch(
    conn: Connection,
    raw: dict[str, Any],
    ctx: DispatchCtx,
    *,
    is_owner: bool = True,
) -> None:
    """Run one inbound ``channel_action`` through the six-step funnel (§4.3).

    Routes to exactly one handler iff the message is rate-ok, a registered type, valid,
    tenant-isolated on its ``meeting_id``, and every entity id it carries is owned by the
    connection's tenant. ANY failure sends a generic error and routes NOTHING.

    The §12.10 ``is_owner`` fence is folded in first: a reclaimed process
    (``is_owner=False``) must not route a side-effecting message, so it refuses silently.
    """
    # ── §12.10 fence: a reclaimed process routes no side-effecting message ──
    if not is_owner:
        return

    # ── 1 · rate-limit per-connection (pinned limits backend, §11.11) ──
    if not ctx.rate_limiter.check(conn.id):
        await conn.send_error(_ERR_SLOW_DOWN)  # generic; no internal detail
        return

    # ── 2 · registry lookup by declared type ──
    declared_type = raw.get("type")
    model = CHANNEL_REGISTRY.get(str(declared_type)) if declared_type is not None else None
    if model is None:
        await conn.send_error(_ERR_NOT_FOUND)  # never "unknown type X" — no info leak
        return

    # ── 3 · Pydantic-validate ONCE, centrally (§4.2) ──
    # A non-UUID meeting_id, an oversized arg, or an out-of-set surface/action is a
    # ValidationError HERE — before any store lookup, which is what makes step 4 sound.
    try:
        msg = model.model_validate(raw)
    except Exception:  # noqa: BLE001 — any validation failure is one generic refusal
        await conn.send_error(_ERR_NOT_FOUND)
        return

    # ── 4 · meeting/tenant isolation, keyed on meeting_id PRESENCE (automatic) ──
    meeting_id = getattr(msg, "meeting_id", None)
    if meeting_id is not None:
        # meeting_id is already a UUID (step 3 rejected non-UUIDs), so this lookup is
        # SOUND — never a query on attacker-shaped input.
        if not await _meeting_owned_by_conn(conn, meeting_id, ctx):
            await conn.send_error(_ERR_NOT_FOUND)  # absent OR foreign → identical generic
            return
    elif getattr(msg, "requires_meeting_scope", True):
        # DEFAULT-REJECT floor: a scoped message with no meeting_id is refused. No V0
        # message opts out; the branch stays as the safety floor for a future global one.
        await conn.send_error(_ERR_NOT_FOUND)
        return

    # ── 5 · entity → owner → tenant, resolved from OUR store, never a client meeting_id ──
    entity_id = getattr(msg, "canvas_id", None) or getattr(msg, "artifact_id", None)
    if entity_id is not None:
        owning_meeting_id = await ctx.store.entity_owning_meeting(entity_id)
        # Resolve the entity's OWN meeting from our store, then tenant-check THAT — the
        # client's meeting_id is never trusted to authorize the entity (kills the smuggle).
        if owning_meeting_id is None or not await _meeting_owned_by_conn(
            conn, owning_meeting_id, ctx
        ):
            await conn.send_error(_ERR_NOT_FOUND)
            return

    # ── 6 · route to the EXACTLY-ONE handler (guaranteed: validated + isolated) ──
    await ctx.handler(conn, msg, ctx)


async def _meeting_owned_by_conn(conn: Connection, meeting_id: UUID, ctx: DispatchCtx) -> bool:
    """True iff ``meeting_id`` resolves (in OUR store) to the connection's authed tenant.

    Absent (no owning tenant) and foreign (a different tenant) both return ``False`` — the
    caller maps both to the identical generic error, so the error never distinguishes them.
    """
    owner_tenant = await ctx.store.meeting_tenant(meeting_id)
    if owner_tenant is None:
        return False
    return str(owner_tenant) == str(conn.tenant_id)


async def resolve_entity_tenant(
    *,
    entity_id: Any,
    entity_type: str,
    principal: dict[str, Any],
    store: Store,
) -> dict[str, Any]:
    """Server-side entity→tenant resolution for a principal (the AC-TEN-002 seam).

    Resolves the entity's OWNING tenant from OUR store and checks it against the
    principal's authed tenant. A cross-tenant read is DENIED — it never resolves into the
    foreign tenant's scope and hands back no foreign data. ``entity_type`` is ``"meeting"``
    (resolve directly) or ``"canvas"``/``"artifact"`` (resolve owning meeting → tenant).

    Returns ``{"allowed": bool, "tenant_id": <owning tenant | None>}``; ``allowed`` is
    ``True`` only when the resolved owning tenant equals the principal's tenant.
    """
    principal_tenant = (
        str(principal.get("tenant_id")) if principal.get("tenant_id") is not None else None
    )
    try:
        eid = entity_id if isinstance(entity_id, UUID) else UUID(str(entity_id))
    except (ValueError, TypeError):
        return {"allowed": False, "tenant_id": None}

    if entity_type == "meeting":
        owner_tenant = await store.meeting_tenant(eid)
    else:
        owning_meeting_id = await store.entity_owning_meeting(eid)
        owner_tenant = (
            await store.meeting_tenant(owning_meeting_id)
            if owning_meeting_id is not None
            else None
        )

    if owner_tenant is None or principal_tenant is None or str(owner_tenant) != principal_tenant:
        # DENY: never resolve into a foreign tenant's scope; hand back no foreign tenant id.
        return {"allowed": False, "tenant_id": None}
    return {"allowed": True, "tenant_id": str(owner_tenant)}


__all__ = [
    "Connection",
    "DispatchCtx",
    "Handler",
    "PerConnectionRateLimiter",
    "Store",
    "dispatch",
    "resolve_entity_tenant",
]
