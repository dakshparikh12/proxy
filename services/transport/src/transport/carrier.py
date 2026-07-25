"""The in-process asyncio carrier (§2, AC-SEAM-06/07, AC-XCUT-06).

The path from transport to the projector/canvas and to the Orchestrator is a direct
in-process ``asyncio`` call — **no bus, no broker, no socket, no wire**. Transport is
imported as a package by the single ``meeting_runtime`` harness process; there is no
separate network service for it. This module is deliberately a thin ``asyncio.Queue``
fan-out — the only carrier primitive on the emit path.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from libs.contracts import AgentChunk

from .projector import carry_turn as _carry_turn
from .signals import Signal


async def drive_projector(
    conn: Any,
    deltas: AsyncIterator[AgentChunk] | Iterable[AgentChunk],
    meeting: Any,
) -> None:
    """Drive the pure-rendering projector over the SAME delta stream (§4.5).

    The carrier's render leg: it feeds the ``BehaviorRunner.run`` delta stream
    (``stream_deltas`` applied ONCE upstream, CANONICAL §11.3 — NEVER re-wrapped here)
    straight into :func:`transport.projector.carry_turn`, which emits
    ``ResponseStart`` → projected frames → ``ResponseEnd`` with the ordering law +
    readyState guard. Kept a thin delegation so the projector owns the mapping and the
    carrier owns only the in-process drive.
    """
    await _carry_turn(conn, deltas, meeting)


class SignalCarrier:
    """Fan one emitted signal stream to in-process subscribers (Doc 03 + Doc 04).

    ``emit`` is a plain awaitable enqueue; ``subscribe`` yields an in-process async
    iterator. No message bus/broker sits between transport and its consumers.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Signal]] = []
        self._closed = False

    async def emit(self, signal: Signal) -> None:
        """Deliver a signal to every in-process subscriber (direct await, no wire)."""
        for queue in self._subscribers:
            await queue.put(signal)

    def subscribe(self) -> AsyncIterator[Signal]:
        """Register an in-process consumer and return its signal iterator."""
        queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._subscribers.append(queue)
        return self._drain(queue)

    async def _drain(self, queue: asyncio.Queue[Signal]) -> AsyncIterator[Signal]:
        while not (self._closed and queue.empty()):
            signal = await queue.get()
            yield signal

    def close(self) -> None:
        self._closed = True
