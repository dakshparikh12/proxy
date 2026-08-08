"""libs.http.ws — the single WebSocket external-call seam (retry + cost telemetry).

Companion to ``external.py``'s ``call_external`` for the ONE case httpx cannot serve: a
long-lived, bidirectional WebSocket round-trip (Cartesia's ``/tts/websocket`` input-
streaming synth is the first caller). The §14 hard rule — *every external call goes
through the single seam in ``libs/http``, wrapped with retry + cost telemetry; no raw
vendor/transport client lives anywhere else* — applies verbatim here:

* ``ws_connect`` is the SOLE construction of the raw ``websockets`` client in the product
  (the exact analogue of ``external.http_client`` / ``external.gcs_bucket``); the SDK is
  imported lazily so a host that only needs the httpx seam never drags it in.
* ``call_external_ws`` wraps the CONNECT in the same bounded-retry-with-backoff +
  cost-telemetry envelope as ``call_external`` (it reuses ``external``'s ``_record_cost``
  and backoff constants so the two seams meter identically), honours a genuine caller
  cancellation immediately (the same ``cancelling()`` guard), yields the LIVE connection,
  and guarantees a clean close on exit — success or fault.

It is deliberately GENERIC (any ``wss://`` service, not Cartesia-specific): the caller
passes the URL + a ``service`` tag and drives ``.send`` / ``.recv`` on the yielded
connection. No product module outside ``libs/http`` may import ``websockets``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from .external import _BASE_BACKOFF_S, _MAX_RETRIES, _record_cost


class WsConnection(Protocol):
    """Structural surface of the raw ``websockets`` client the seam yields.

    ``send`` accepts one text/binary frame; ``recv`` returns the next inbound frame
    (text arrives as ``str``, binary as ``bytes``); ``close`` shuts the socket. Callers
    type against THIS, never against ``websockets`` — the SDK stays behind the seam.
    """

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


def ws_connect(url: str, **kwargs: Any) -> Any:
    """The ONLY construction of the raw ``websockets`` client in the product.

    Returns the awaitable ``connect(...)`` handle (``await`` it for the live
    ``ClientConnection``). The SDK is imported lazily HERE — never at import/boot time —
    so a host that only needs the ``call_external`` httpx seam does not drag in
    ``websockets``; this is the sole legitimate home for the raw ws-client construction,
    and no product module outside ``libs/http`` may import it.
    """
    from websockets.asyncio.client import connect

    return connect(url, **kwargs)


@asynccontextmanager
async def call_external_ws(
    url: str,
    *,
    service: str,
    unit_cost_usd: float = 0.0,
    max_retries: int = _MAX_RETRIES,
    open_timeout: float = 10.0,
    **connect_kwargs: Any,
) -> AsyncIterator[WsConnection]:
    """Open ONE external WebSocket through the seam: retry the connect (backoff) + meter
    it, yield the live connection, close it cleanly on exit.

    The CONNECT is the retried unit (a live send/recv loop can't be transparently
    replayed): a transient transport error (``OSError`` / ``TimeoutError``) backs off and
    reconnects up to ``max_retries``; every attempt is metered and the accumulated cost
    recorded via the shared ``_record_cost`` hook, exactly like ``call_external``. A
    ``CancelledError`` is honoured immediately when THIS task is genuinely being cancelled
    (``cancelling() > 0`` — e.g. meeting-end drain), and otherwise treated as a transport
    blip and retried (the same rule ``call_external`` applies at the httpx seam). A
    non-transient failure (e.g. an auth handshake rejection) is NOT retried — it raises
    honestly (Law 2). The connection is always closed on context exit.
    """
    attempt = 0
    last_exc: BaseException | None = None
    conn: Any = None
    while attempt < max_retries:
        attempt += 1
        try:
            conn = await ws_connect(url, open_timeout=open_timeout, **connect_kwargs)
        except (OSError, TimeoutError) as exc:
            last_exc = exc
            await asyncio.sleep(_BASE_BACKOFF_S * float(attempt))  # backoff, then reconnect
            continue
        except asyncio.CancelledError as exc:
            # Genuine caller cancellation (cancelling() > 0) is honoured at once; a
            # cancelling()==0 CancelledError is a transport-induced blip → retry.
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            last_exc = exc
            await asyncio.sleep(_BASE_BACKOFF_S * float(attempt))  # backoff, then reconnect
            continue
        _record_cost(service, unit_cost_usd, attempt)
        break
    else:
        assert last_exc is not None  # noqa: S101 - loop invariant: a failed connect set it
        raise last_exc
    try:
        yield conn
    finally:
        await conn.close()  # honest clean close — success or fault
