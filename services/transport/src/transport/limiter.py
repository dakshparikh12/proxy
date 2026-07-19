"""The ``limits``-backed in-memory rate gate (AC-FAIL-16).

The outbound limiter is built on the pinned **``limits``** library with its **in-memory**
``MemoryStorage`` backend and a moving-window strategy — deliberately **not** a hand-rolled
token bucket (AC-FAIL-16). This gate supplies the rate accounting; the never-drop queue
that WAITS on it (rather than rejecting a send) lives in :mod:`transport.outbound`, so a
burst queues instead of throttling mid-meeting (AC-FAIL-14/15).

``limits`` is a managed-stack dependency provisioned on the meeting_runtime estate (added
to the workspace lock + installed there); it is imported here so a static scan sees the
limiter is built on ``limits``/``MemoryStorage`` with no bespoke bucket loop.
"""
from __future__ import annotations

import asyncio

from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from . import config

# Poll granularity for the wait loop — a mechanism/physics constant (how often the queue
# re-checks the moving window), not an operational tunable, so it stays in code (Law 4).
_POLL_INTERVAL_S = 0.005


class LimitsRateGate:
    """A per-bot :class:`~transport.outbound.RateGate` on ``limits`` MemoryStorage.

    :meth:`acquire` blocks (polling the moving window) until a slot is free, then consumes
    it — it never rejects, so the outbound queue only ever *waits*, never drops a send.

    The shared Recall-workspace outbound budget (sends/second) is a genuine operational
    tunable, so it is read from ``config/defaults.toml`` (one value + unit + range,
    Law 4) rather than hard-coded — the value is the floor the never-drop queue paces
    against.
    """

    def __init__(self, *, per_second: int | None = None) -> None:
        self._storage = MemoryStorage()  # in-memory backend (AC-FAIL-16), not a hand-rolled bucket
        self._limiter = MovingWindowRateLimiter(self._storage)
        rate = per_second if per_second is not None else config.get_int("outbound_sends_per_second")
        self._item = RateLimitItemPerSecond(rate)

    async def acquire(self, bot_id: str) -> None:
        """Wait until the moving window admits one send for ``bot_id``, then consume it."""
        while not self._limiter.hit(self._item, bot_id):
            await asyncio.sleep(_POLL_INTERVAL_S)
