"""libs.http.gateway — WS auth at the connection UPGRADE (401 before the 101, §4.3/§12.9).

Auth is guaranteed at the connection upgrade, **never per-message** — a WS that
authenticates per-message instead of per-connection is the classic hole (§4.3). This
module owns ``authorize_upgrade``: it resolves the signed session cookie to a
principal SERVER-SIDE, checks the origin allowlist and the per-user connection cap,
and either returns the authenticated :class:`Connection` (which the dispatch funnel
then trusts for isolation) or raises :class:`RejectUpgrade` BEFORE the socket opens.

The session resolver and the origin/limit policy are INJECTED (a callable + config),
so ``libs/http`` stays free of a hard dependency on the ``sessions`` table plumbing:
the live control_plane mount binds ``resolve_session`` to the harness session reader
(``control_plane.session.resolve_session`` over ``app.state.db``), and the tests bind a
fake resolver. The gateway owns only the ORDER and the fail-closed rejection.

Isolation lineage: the ``tenant_id`` on the returned :class:`Connection` is the one
the session resolved to server-side — it is never a client-supplied field, which is
exactly what the funnel's meeting/entity isolation relies on (§4.3).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# The per-user connection cap (§4.3): a single user may not open unbounded sockets.
MAX_CONN_PER_USER = 8


class RejectUpgrade(Exception):
    """Reject a WS upgrade BEFORE the 101 handshake with an HTTP status.

    The status is the wire status the gateway returns instead of switching protocols:
    ``401`` (no/invalid session), ``403`` (disallowed origin), ``429`` (per-user cap).
    Raising — never returning a half-open socket — is what makes the reject happen
    strictly before the 101 (the socket never opens on an unauthenticated upgrade).
    """

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(detail or f"upgrade rejected ({status})")
        self.status = status


@dataclass(frozen=True)
class Connection:
    """An authenticated WS connection — the principal every inbound message rides on.

    ``tenant_id`` is resolved SERVER-SIDE at upgrade (from the signed session), never a
    client-supplied field; the dispatch funnel's meeting/entity isolation checks every
    entity id against THIS tenant. ``id`` is the per-connection rate-limit key (§4.3).
    """

    user_id: Any
    tenant_id: Any
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    async def send_error(self, message: str) -> None:
        """Send a generic error frame to the client (overridden by the live socket).

        The base is a no-op sink so a Connection is usable in a handler-free context;
        the live transport binds a real socket writer. The funnel only ever passes the
        two generic strings ``"Not found"`` / ``"Slow down."`` here — never a leak.
        """
        return None


# The injected session resolver: signed cookies -> {user_id, tenant_id} | None.
ResolveSession = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class ConnLimiter(Protocol):
    """Per-user connection counter (the §4.3 per-user cap). Injected; None disables."""

    def count(self, user_id: Any) -> int: ...


def _origin_allowed(origin: str | None, allowed_origins: tuple[str, ...] | None) -> bool:
    """True when no allowlist is configured (dev), or the origin is on the allowlist."""
    if not allowed_origins:
        return True
    return origin in allowed_origins


async def authorize_upgrade(
    request: Any,
    *,
    resolve_session: ResolveSession,
    allowed_origins: tuple[str, ...] | None = None,
    conn_limiter: ConnLimiter | None = None,
) -> Connection:
    """Authorize a WS upgrade and return the :class:`Connection`, or raise before the 101.

    Order (fail-closed):

    1. resolve the signed session cookie SERVER-SIDE — no/invalid session ⇒ ``401``;
    2. origin allowlist (prod) — a disallowed origin ⇒ ``403``;
    3. per-user connection cap — at/over the cap ⇒ ``429``.

    Every rejection is a :class:`RejectUpgrade` raised BEFORE any socket is accepted, so
    an unauthenticated upgrade never reaches the 101 handshake and no per-message check
    ever runs on an unauthenticated connection.
    """
    cookies = dict(getattr(request, "cookies", {}) or {})
    session = await resolve_session(cookies)
    if session is None:
        raise RejectUpgrade(401)  # reject BEFORE the socket opens

    headers = getattr(request, "headers", {}) or {}
    origin = headers.get("origin") if hasattr(headers, "get") else None
    if not _origin_allowed(origin, allowed_origins):
        raise RejectUpgrade(403)

    user_id = session["user_id"]
    if conn_limiter is not None and conn_limiter.count(user_id) >= MAX_CONN_PER_USER:
        raise RejectUpgrade(429)  # per-user connection cap

    return Connection(user_id=user_id, tenant_id=session["tenant_id"])


__all__ = [
    "MAX_CONN_PER_USER",
    "Connection",
    "RejectUpgrade",
    "ResolveSession",
    "authorize_upgrade",
]
