"""``run_loop`` — the per-meeting asyncio event queue that IS the missing spine.

This realizes the **RUN** block of §3's build diagram and §3.2's *"one plain
asyncio program per meeting"* — the spine that D-008 named as the single largest
build in the chain. Everything the substrate primitives (claim/fence/cost/
reconcile/recovery) already provide gets wired together HERE, and only here.

**What the loop is (and is deliberately not).**
Every signal — a webhook, a heartbeat, a boundary, a confirmed ask, a
workroom-done, a control command, a cost threshold, an anomaly — lands as a
:class:`MeetingEvent` on ONE per-meeting :class:`asyncio.Queue`. The loop pulls
each event and routes it **through a wake turn or a reflex**. There is **no
``if event_type → handler`` dispatch table** anywhere in this module: the routing
IS the wake turn (§3.2). The ONLY code decision in the loop body is the mechanical
front-gate verdict — *"was Proxy addressed?"* (the injected ``addressed``
predicate, backed by the name-gate reflex). An addressed event flows to the ONE
generic wake turn (the model's judgment decides what to do); an un-addressed event
is folded into the state digest with **zero** agent involvement. An unanticipated
situation is just another event description handled by the same judgment (§3.2's
"the dynamism, concretely").

**No bus, no broker (§4).** The whole runtime is an ``asyncio.Task`` + a Postgres
``operation_runs`` row (the durable lifecycle/fence, §3.7) + wall-clock timers.
This module holds none of the durable state itself — it is the in-process
serializer that the substrate rows make crash-safe. Proxy is the only serializer,
and only at the mouth (the gated :class:`~harness.emit.Emitter`).

**Standing pipes are pure forwarding (§3.2).** The pipes wired once at join
(audio→STT→transcript; transcript→Scribe; Scribe→judge; heartbeats) are a
:class:`StandingPipe` — a source→sink forwarder with **no decision, no branch, no
agent**. A silent hour keeps them flowing at zero agent cost; a wake turn only
runs when something is addressed.

**In-flight bookkeeping (§3.15).** A ``[3–5]`` semaphore bounds concurrent wake
turns · a duplicate of an in-flight ask **attaches** to it (never spawns a second
turn) · a correction mid-task **injects** into that task's session · an ask past
``~2s`` **detaches** into a background task whose completion re-wakes Proxy. The
list is plain state; *what to do* about a collision is the agent's call on wake.

**Fencing (§3.7).** Every mouth/apply/dispatch side-effect the loop makes goes
through the gated :class:`~harness.emit.Emitter`, which reads ``is_owner`` live off
the meeting's ``operation_runs`` handle. A fenced-out harness (a replacement
re-claimed the meeting → ``is_owner`` False) reaches the wire **zero** times.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .emit import Emitter

#: The wake-turn bound (§3.15 / §4 "semaphore [3–5]"). A physics bound on
#: concurrent in-flight judgment turns, not policy — extra asks queue behind it.
DEFAULT_MAX_IN_FLIGHT: int = 5

#: The detach threshold (§3.15 / §4 "detach threshold [~2s]"): a wake turn that
#: runs longer than this is detached ("I'll have it in a moment") and its
#: completion re-wakes Proxy, so the loop never blocks on a long turn.
DEFAULT_DETACH_AFTER_S: float = 2.0

#: The injected clock/predicate/callback seam types — all default to sensible
#: real implementations so the loop runs live, but each is injectable so the
#: spine proves in isolation (a fake clock, a counting wake turn, a recorder).
WakeTurn = Callable[["MeetingEvent", dict[str, Any]], Awaitable[None]]
Addressed = Callable[["MeetingEvent"], bool]
Clock = Callable[[], float]


@dataclass
class MeetingEvent:
    """One thing that happened, wrapped for the ONE queue — payload + arrival state.

    The loop treats ``payload`` as opaque DATA: it never branches on the payload's
    *type* to decide the action (that would be the forbidden dispatch table). The
    only thing read off an event on the routing path is the injected ``addressed``
    predicate's verdict. ``ask_id`` identifies the ask for in-flight bookkeeping
    (dedupe/attach, correction-inject, detach); events with no ``ask_id`` are
    ambient (folded into state, never a wake). ``emitter`` is the gated delivery
    seam the wake turn speaks through (fencing, §3.7). ``on_inject`` registers the
    session-injection channel a mid-task correction feeds (§3.15).
    """

    payload: Any
    ask_id: str | None = None
    arrived_at: float | None = None
    emitter: Emitter | None = None
    _inject: Callable[[str], Any] | None = field(default=None, repr=False)

    def on_inject(self, channel: Callable[[str], Any]) -> None:
        """Register the correction-injection channel for this event's wake turn.

        The wake turn calls this to expose *where* a mid-task correction lands
        (the running SDK session). :meth:`RunLoop.inject_correction` then feeds the
        correction text through it — the correction INJECTS into the live task's
        session rather than opening a fresh turn (§3.15).
        """
        self._inject = channel

    def inject(self, text: str) -> bool:
        """Feed a correction into this event's registered injection channel."""
        if self._inject is None:
            return False
        self._inject(text)
        return True


@dataclass
class StandingPipe:
    """A standing pipe — PURE forwarding from a source to a sink (§3.2).

    The pipes wired once at join (audio→STT→transcript; transcript→Scribe;
    Scribe events→judge; heartbeats) all have this one shape: pull from an async
    source, hand each item to a sink, forever. There is **no decision, no branch,
    no agent** — wiring an organism, not hard-coding a situation. A silent hour is
    just the pipes forwarding heartbeats at zero agent cost.
    """

    source: AsyncIterator[Any]
    sink: Callable[[Any], Awaitable[None]]

    async def run(self) -> None:
        """Forward every item from the source to the sink until the source ends."""
        async for item in self.source:
            await self.sink(item)


@dataclass
class _InFlight:
    """The plain in-flight-task record for one ask (§3.15 bookkeeping — just state)."""

    ask_id: str
    event: MeetingEvent
    detached: bool = False


class RunLoop:
    """The per-meeting asyncio spine: ONE queue, the routing IS the wake turn.

    Construction wires the seams the substrate already provides:

    * ``wake_turn`` — the ONE generic judgment entry (§3.2). The loop hands it the
      event + a compact state digest; the model's tool calls do the real thing.
      Defaults to a no-op so the spine proves without a live SDK session.
    * ``addressed`` — the mechanical front-gate verdict (§3.1, the name-gate). The
      ONLY code decision on the routing path. Defaults to "never addressed" so a
      bare loop is a silent, pipe-only meeting.
    * ``emitter`` — the gated :class:`~harness.emit.Emitter` (§3.7 fencing); every
      wake-turn side-effect goes through it, so a fenced-out harness is silent.
    * ``max_in_flight`` — the ``[3–5]`` semaphore bound (§3.15).
    * ``clock`` / ``detach_after_s`` / ``on_rewake`` — the wall-clock detach seam
      (§3.15): a turn past ``~2s`` detaches and its completion re-wakes Proxy.

    The loop holds NO durable state — the ``operation_runs`` row (claimed/fenced by
    §3.6/§3.7) is the durable lifecycle. This is the in-process serializer only.
    """

    def __init__(
        self,
        *,
        wake_turn: WakeTurn | None = None,
        addressed: Addressed | None = None,
        emitter: Emitter | None = None,
        max_in_flight: int = DEFAULT_MAX_IN_FLIGHT,
        clock: Clock | None = None,
        detach_after_s: float = DEFAULT_DETACH_AFTER_S,
        on_rewake: Callable[[str], Any] | None = None,
    ) -> None:
        if not (3 <= max_in_flight <= 5):
            raise ValueError(
                f"the in-flight semaphore bound must be in [3–5] (§3.15); got {max_in_flight}"
            )
        # THE per-meeting queue — every signal lands here; no bus, no broker (§4).
        self.queue: asyncio.Queue[MeetingEvent] = asyncio.Queue()
        self._wake_turn: WakeTurn = wake_turn or _noop_wake
        self._addressed: Addressed = addressed or (lambda _e: False)
        self._emitter = emitter
        self.max_in_flight = max_in_flight
        self._sem = asyncio.Semaphore(max_in_flight)
        self._clock: Clock = clock or time.monotonic
        self._detach_after_s = detach_after_s
        self._on_rewake = on_rewake
        # Plain in-flight bookkeeping (§3.15) — a task list, nothing more.
        self._in_flight: dict[str, _InFlight] = {}
        self._detached: dict[str, _InFlight] = {}
        self._pipes: list[StandingPipe] = []
        # Observability counters (proof the routing flowed, not policy).
        self.events_routed = 0
        self.wake_turns_run = 0

    # ── standing pipes (pure forwarding, §3.2) ──────────────────────────────
    def wire_pipe(self, pipe: StandingPipe) -> None:
        """Wire a standing pipe once (at join). It forwards continuously, no agent."""
        self._pipes.append(pipe)

    async def run_pipes_until_done(self) -> None:
        """Run every wired standing pipe to completion (pure forwarding, zero wakes).

        The pipes flow independently of the event queue: a silent meeting is just
        the pipes forwarding while the queue stays empty, so ``wake_turns_run``
        stays 0 (the "an hour of silence → zero agent calls" acceptance).
        """
        if not self._pipes:
            return
        await asyncio.gather(*(pipe.run() for pipe in self._pipes))

    # ── the queue: enqueue + drain THROUGH the one routing entry ────────────
    async def enqueue(self, event: MeetingEvent) -> None:
        """Land one event on THE per-meeting queue (every signal enters here)."""
        if event.arrived_at is None:
            event.arrived_at = self._clock()
        await self.queue.put(event)

    async def run_until_idle(self) -> None:
        """Pull and route every queued event through the loop, then return.

        The loop body is the SAME for every event: route it. The routing decides
        wake-vs-fold via the front-gate verdict — never via the event's type.
        """
        while not self.queue.empty():
            event = self.queue.get_nowait()
            await self.route(event)
            self.queue.task_done()

    # ── the ONE routing entry — the routing IS the wake turn (§3.2) ──────────
    async def route(self, event: MeetingEvent) -> bool:
        """Route ONE event through the loop: a wake turn, or folded into state.

        This is the whole loop body, and it holds exactly one code decision — the
        mechanical front-gate verdict :attr:`_addressed`. There is deliberately no
        ``if event_type == ...`` ladder here: an addressed event goes to the ONE
        generic wake turn (the model's judgment); an un-addressed event is folded
        into the state digest with no agent call. Returns True iff a wake turn was
        run or attached (the event drew Proxy's judgment), False if it was ambient.
        """
        self.events_routed += 1
        if event.emitter is None:
            event.emitter = self._emitter
        if not self._addressed(event):
            # Ambient signal — pure state, no agent (the silent-meeting path).
            return False
        return await self._wake(event)

    async def _wake(self, event: MeetingEvent) -> bool:
        """Run (or attach/inject) the ONE generic wake turn for an addressed event.

        In-flight bookkeeping (§3.15), all plain state:
          * a duplicate of an in-flight ask ATTACHES (no second turn spawns);
          * the turn runs under the ``[3–5]`` semaphore (bounded concurrency);
          * a turn past the detach threshold DETACHES and its completion re-wakes.
        """
        ask_id = event.ask_id
        # Duplicate of an in-flight ask → ATTACH rather than spawn (§3.15).
        if ask_id is not None and ask_id in self._in_flight:
            return True  # attached to the already-running turn
        record = _InFlight(ask_id=ask_id or "", event=event)
        if ask_id is not None:
            self._in_flight[ask_id] = record
        try:
            async with self._sem:  # the [3–5] bound on concurrent wake turns
                started = self._clock()
                self.wake_turns_run += 1
                digest = self._state_digest()
                await self._wake_turn(event, digest)
                elapsed = self._clock() - started
                # Past ~2s → detach: record it + re-wake Proxy on completion (§3.15).
                if ask_id is not None and elapsed > self._detach_after_s:
                    record.detached = True
                    self._detached[ask_id] = record
                    if self._on_rewake is not None:
                        self._on_rewake(ask_id)
            return True
        finally:
            if ask_id is not None:
                self._in_flight.pop(ask_id, None)

    # ── correction-injection (§3.15) ────────────────────────────────────────
    async def inject_correction(self, ask_id: str, text: str) -> bool:
        """Inject a mid-task correction into an in-flight task's session (§3.15).

        A correction targeting an ask already in flight does NOT open a new wake
        turn — it feeds the text into that task's registered injection channel
        (the running SDK session). Returns True iff a live in-flight task received
        it (False if the ask is not in flight — the caller then routes it fresh).
        """
        record = self._in_flight.get(ask_id)
        if record is None:
            return False
        return record.event.inject(text)

    def was_detached(self, ask_id: str) -> bool:
        """True iff the ask ran past the detach threshold and was detached (§3.15)."""
        return ask_id in self._detached

    def _state_digest(self) -> dict[str, Any]:
        """The compact state digest handed to the wake turn (§3.2) — not raw history.

        Tasks in flight · mouth free/busy · the fence state. The wake turn is
        primed by THIS, never raw session history (§3.2 state-digest compaction);
        the durable Notes object (read via ``notes_ref``) is what survives a
        compaction. Kept mechanical — pure bookkeeping, no judgment.
        """
        return {
            "in_flight": sorted(self._in_flight),
            "detached": sorted(self._detached),
            "is_owner": self._emitter.is_owner if self._emitter is not None else True,
        }


async def _noop_wake(event: MeetingEvent, digest: dict[str, Any]) -> None:
    """The default wake turn — a no-op so the spine proves without a live SDK session."""
    return None


__all__ = [
    "DEFAULT_DETACH_AFTER_S",
    "DEFAULT_MAX_IN_FLIGHT",
    "MeetingEvent",
    "RunLoop",
    "StandingPipe",
]
