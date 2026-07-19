"""Per-bot outbound rate limiter/queue (§3.7, AC-FAIL-14/15).

Recall's workspace request limit is shared across Output-Media + chat, so concurrent
deliveries could throttle mid-meeting. This layer paces every outbound send through a
**per-bot queue** that **waits** on a rate gate rather than rejecting — a burst queues
instead of throttling, and every submitted delivery is eventually delivered (never
dropped by the shared limit). The rate accounting itself is delegated to a pluggable
:class:`RateGate` (the ``limits``-backed in-memory gate lives in :mod:`transport.limiter`,
AC-FAIL-16) — this module owns only the never-drop queue mechanism.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

#: One outbound send (an Output-Media or chat round-trip), deferred so the queue can pace it.
Delivery = Callable[[], Awaitable[None]]


@runtime_checkable
class RateGate(Protocol):
    """Acquire one send slot for a bot — RETURNS when allowed, never rejects (waits)."""

    async def acquire(self, bot_id: str) -> None: ...


class PermissiveGate:
    """A no-op gate (unbounded) — the default when no shared-limit pacing is configured."""

    async def acquire(self, bot_id: str) -> None:  # noqa: D401 - trivial
        return None


class OutboundQueue:
    """A per-bot FIFO that paces sends through a :class:`RateGate` and never drops one.

    Every :meth:`submit` is enqueued; the worker drains in order, waiting on the gate
    before each send. A burst therefore queues (``depth > 0``) and all deliveries land
    (``delivered == submitted``); ``dropped_by_throttle`` stays 0 — the shared limit
    paces, it never rejects mid-meeting (AC-FAIL-14/15).
    """

    def __init__(self, gate: RateGate | None = None) -> None:
        self._gate: RateGate = gate if gate is not None else PermissiveGate()
        self._queues: dict[str, asyncio.Queue[Delivery]] = {}
        self.delivered = 0
        self.dropped_by_throttle = 0
        self.max_observed_depth = 0

    def submit(self, bot_id: str, delivery: Delivery) -> None:
        """Enqueue an outbound delivery for a bot — never rejected, always queued."""
        queue = self._queues.setdefault(bot_id, asyncio.Queue())
        queue.put_nowait(delivery)
        self.max_observed_depth = max(self.max_observed_depth, self._total_depth())

    async def drain(self, bot_id: str) -> None:
        """Deliver every queued send for a bot in order, pacing through the gate."""
        queue = self._queues.get(bot_id)
        if queue is None:
            return
        while not queue.empty():
            delivery = queue.get_nowait()
            await self._gate.acquire(bot_id)  # waits under the shared limit; never rejects
            await delivery()
            self.delivered += 1

    def depth(self, bot_id: str) -> int:
        queue = self._queues.get(bot_id)
        return queue.qsize() if queue is not None else 0

    def _total_depth(self) -> int:
        return sum(q.qsize() for q in self._queues.values())
