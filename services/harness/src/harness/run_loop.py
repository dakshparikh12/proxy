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
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agentkit.abort import KEY_SEP, AbortRegistry

from .emit import Emitter

#: The wake-turn bound (§3.15 / §4 "semaphore [3–5]"). A physics bound on
#: concurrent in-flight judgment turns, not policy — extra asks queue behind it.
DEFAULT_MAX_IN_FLIGHT: int = 5

#: The detach threshold (§3.15 / §4 "detach threshold [~2s]"): a wake turn that
#: runs longer than this is detached ("I'll have it in a moment") and its
#: completion re-wakes Proxy, so the loop never blocks on a long turn.
DEFAULT_DETACH_AFTER_S: float = 2.0

#: The per-task HARD timeout (§3.11 "a hard per-task timeout … Orchestrator answer
#: ~4–5s") — RESCALED to seconds for the interactive loop (not the SDK's 5-min).
#: A wake that overruns this bound has its :class:`AbortController` fired: a stalled
#: meeting is worse than a dropped note. This is the answer-turn bound (in-window for
#: the [4s, 5s] answer p95); a Scribe/dispatch turn passes its own (~3–4s) via
#: ``wake_timeout_s``. Distinct from the detach threshold (~2s) — detach hands control
#: back, the timeout ABORTS the turn.
DEFAULT_WAKE_TIMEOUT_S: float = 4.0

#: The watchdog's poll cadence (seconds of REAL event-loop time between two reads of the
#: injected clock). The watchdog measures the bound against the INJECTED clock — not by
#: sleeping the whole bound — so a mocked clock drives the boundary deterministically and
#: the abort's latency is recorded off that clock. Small enough that the live monotonic
#: clock crosses the bound promptly (sub-poll overshoot), yet a real yield each tick.
_WATCHDOG_POLL_S: float = 0.01

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
    #: The live model-loop :class:`~agentkit.abort.AbortController` for THIS wake (§3.11).
    #: The loop mints it via ``registry.make(meeting_id|ask_id)`` and threads it here so
    #: the wake turn passes the REAL controller down the seam (the provider polls
    #: ``.aborted`` to break its SDK loop) — never the bare ``_Abort()`` that never fires.
    #: ``None`` on an ambient event that never wakes Proxy.
    abort: Any = None
    #: The measured elapsed (MILLISECONDS, on the loop's injected clock) from turn-start
    #: to the moment the per-task hard timeout fired this wake's abort (§3.11). ``None``
    #: until/unless the WATCHDOG fires — a turn that finishes under its bound never sets
    #: it. This is the latency oracle the boundary criterion reads: an answer wake lands
    #: in [4000, 5000], a Scribe wake in [3000, 4000]. Recorded off the INJECTED clock so
    #: the window is measured deterministically, not against wall-clock jitter.
    abort_fired_after_ms: float | None = None
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
    """The plain in-flight-task record for one ask (§3.15 bookkeeping — just state).

    ``task`` is the background :class:`asyncio.Task` the wake turn runs in. The turn
    ALWAYS runs in a task so the loop can hand back control the instant the detach
    threshold passes — a turn that outlives ``~2s`` keeps running in this task (still
    holding its ``[3–5]`` slot) while ``route`` returns, and the task's completion
    re-wakes Proxy (§3.15). ``detached`` records that a re-wake is owed on completion.
    """

    ask_id: str
    event: MeetingEvent
    detached: bool = False
    task: asyncio.Task[None] | None = None


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
        registry: AbortRegistry | None = None,
        meeting_id: str = "",
        wake_timeout_s: float = DEFAULT_WAKE_TIMEOUT_S,
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
        # The ONE abort registry (§11.9) — imported, never redefined. The loop mints a
        # per-wake AbortController through it (``registry.make(meeting_id|ask_id)``) and
        # threads it into the wake turn, so meeting-end / "Proxy, quiet" / the per-task
        # timeout can halt the LIVE model loop. Defaults to a fresh one so the spine runs
        # standalone; the meeting runtime passes its shared registry so end/quiet reach here.
        self._abort_registry: AbortRegistry = registry if registry is not None else AbortRegistry()
        self._meeting_id = meeting_id
        # The per-task hard timeout (§3.11) — a wake past this bound has its controller
        # aborted (a stalled meeting is worse than a dropped note). Non-positive disables.
        self._wake_timeout_s = wake_timeout_s
        # ask_id → the registry key of its live controller, so the barge-in / "Proxy,
        # quiet" path can cancel the addressed in-flight model loop by ask_id (§3.11).
        self._task_keys: dict[str, str] = {}
        # ask_id → the measured elapsed (ms, on the injected clock) at which its per-task
        # hard timeout fired the abort (§3.11). Outlives the turn (the controller retires on
        # completion) so the boundary-value latency oracle / reconcile can read it.
        self._abort_latency_ms: dict[str, float] = {}
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

    # ── the abort registry — the ONE §11.9 home, threaded into every wake ────
    @property
    def abort_registry(self) -> AbortRegistry:
        """The shared abort registry the loop mints per-wake controllers through (§3.11)."""
        return self._abort_registry

    def cancel_ask(self, ask_id: str) -> bool:
        """Abort the in-flight model loop for one ask (barge-in / "Proxy, quiet", §3.11).

        The transport barge-in path calls this in ADDITION to the sub-200ms TTS speech
        cut: it cancels the addressed ask's live :class:`AbortController` so the MODEL
        loop halts (not just the speech). Returns True iff a live controller was cancelled
        for that ask; a no-op (False) if the ask has no in-flight turn. The speech cut is
        the transport turn-core's job and is never touched here — this ADDS the model kill.
        """
        key = self._task_keys.get(ask_id)
        if key is None:
            return False
        self._abort_registry.cancel(key)
        return True

    # ── the ONE routing entry — the routing IS the wake turn (§3.2) ──────────
    async def route(self, event: MeetingEvent, *, force_new: bool = False) -> bool:
        """Route ONE event through the loop: a wake turn, or folded into state.

        This is the whole loop body, and it holds exactly one code decision — the
        mechanical front-gate verdict :attr:`_addressed`. There is deliberately no
        ``if event_type == ...`` ladder here: an addressed event goes to the ONE
        generic wake turn (the model's judgment); an un-addressed event is folded
        into the state digest with no agent call. Returns True iff a wake turn was
        run or attached (the event drew Proxy's judgment), False if it was ambient.

        ``force_new`` forces a FRESH judgment-moment even for an ask already in flight:
        it mints a new controller via ``registry.make`` (preempting the stale one, §3.11)
        rather than attaching — the harness sets it when a re-ask supersedes the prior
        turn. The default (attach a duplicate, §3.15) is unchanged.
        """
        self.events_routed += 1
        if event.emitter is None:
            event.emitter = self._emitter
        if not self._addressed(event):
            # Ambient signal — pure state, no agent (the silent-meeting path).
            return False
        return await self._wake(event, force_new=force_new)

    async def _wake(self, event: MeetingEvent, *, force_new: bool = False) -> bool:
        """Run (or attach/inject) the ONE generic wake turn for an addressed event.

        In-flight bookkeeping (§3.15), all plain state:
          * a duplicate of an in-flight ask ATTACHES (no second turn spawns);
          * the turn runs in a background task under the ``[3–5]`` semaphore;
          * a turn past the detach threshold DETACHES — ``route`` hands back control
            with the turn STILL running (the loop never blocks on a long turn), and
            the background task's completion re-wakes Proxy.

        A real detach, not post-hoc bookkeeping: the turn ALWAYS runs in its own
        :class:`asyncio.Task`, and this method waits for *either* the task to finish
        *or* the ``detach_after_s`` threshold to elapse — whichever comes first. A
        fast turn (under the threshold) is awaited to completion inline exactly as a
        synchronous call would be, so its side effects are on the wire when ``route``
        returns. A slow turn crosses the threshold with the task still running:
        ``route`` returns NOW (control back to the loop), the ask is recorded
        detached, and a completion callback re-wakes Proxy when the turn finally ends.
        """
        ask_id = event.ask_id
        # Duplicate of an in-flight ask → ATTACH rather than spawn (§3.15) — UNLESS the
        # caller forces a fresh judgment-moment (a re-ask that supersedes the stale turn),
        # which preempts via make() below rather than attaching.
        if ask_id is not None and ask_id in self._in_flight and not force_new:
            return True  # attached to the already-running turn
        record = _InFlight(ask_id=ask_id or "", event=event)
        if ask_id is not None:
            self._in_flight[ask_id] = record

        # Mint the LIVE model-loop controller for this wake through the ONE registry
        # (§3.11 / §11.9): keyed ``meeting_id|ask_id``, ``make()`` preempts any stale
        # controller for that key (a fresh judgment-moment cancels the superseded turn so
        # it stops burning budget). Threaded onto the event so the wake turn passes the
        # REAL controller down the seam — the provider polls ``.aborted`` to break its SDK
        # loop — never the bare ``_Abort()`` that never fires. ``cancel_ask`` (barge-in /
        # "Proxy, quiet") and the meeting-end ``cancel_meeting`` both reach THIS controller.
        key = self._controller_key(ask_id)
        event.abort = self._abort_registry.make(key)
        if ask_id is not None:
            self._task_keys[ask_id] = key

        # The turn runs in its OWN task; it acquires the [3–5] slot inside the task
        # and holds it for as long as it runs — a detached turn keeps its slot while
        # it finishes in the background, but the loop (this coroutine) does not block.
        turn = asyncio.ensure_future(self._run_turn(event, record))
        record.task = turn

        # Wait for the turn to finish OR the detach threshold to pass — whichever is
        # first. The timeout is real event-loop time; a turn that is genuinely long
        # (still awaiting a gate/tool) is not done when the threshold fires → detach.
        done, _pending = await asyncio.wait({turn}, timeout=self._detach_after_s)

        if turn in done:
            # The turn finished within the threshold on the real clock. It is a
            # normal fast turn UNLESS the injected clock reports it ran past the
            # threshold (the simulated-long-turn path): then it, too, detaches, and
            # because it is ALREADY complete its detach and re-wake coincide here.
            if ask_id is not None:
                self._in_flight.pop(ask_id, None)
                self._task_keys.pop(ask_id, None)  # the finished turn's controller retires
                if record.detached:
                    self._record_detached(ask_id, record)
                    self._rewake(ask_id)
            turn.result()  # re-raise any error from the turn (fast path)
            return True

        # DETACH: the turn is still running past the threshold. Hand control back to
        # the loop NOW; the turn keeps running in its task (still holding its slot).
        # The re-wake is owed to the turn's COMPLETION (not to this detach moment),
        # so it fires from the done-callback — the loop never blocks on the long turn.
        if ask_id is not None:
            self._record_detached(ask_id, record)

        def _completed(finished: asyncio.Task[None], aid: str | None = ask_id) -> None:
            self._on_turn_complete(aid, finished)

        turn.add_done_callback(_completed)
        return True

    def _controller_key(self, ask_id: str | None) -> str:
        """The registry key ``meeting_id|ask_id`` for a wake's controller (§3.11).

        Splitting on :data:`~agentkit.abort.KEY_SEP` is how ``cancel_meeting`` scopes to
        one meeting, so the key ALWAYS carries the meeting id even for an ask-less wake
        (keyed ``meeting_id|`` — still cancelled by the meeting, never by a sibling)."""
        return f"{self._meeting_id}{KEY_SEP}{ask_id or ''}"

    async def _run_turn(self, event: MeetingEvent, record: _InFlight) -> None:
        """Run the ONE generic wake turn under the ``[3–5]`` semaphore (§3.15).

        Runs inside a background task so the loop can detach from it. Acquiring the
        slot HERE (not in the caller) means a detached, still-running turn keeps its
        slot until it truly finishes — the bound counts in-flight turns, detached or
        not. Records the injected-clock elapsed so a turn that ran past the threshold
        (even if it returned fast on the real clock) is marked detached.

        The wake is guarded by the per-task HARD timeout (§3.11): a WATCHDOG fires the
        turn's :class:`AbortController` once the ``wake_timeout_s`` bound elapses (a
        stalled meeting is worse than a dropped note). The abort is cooperative — the
        provider polls ``.aborted`` and breaks its SDK loop — so the turn unwinds on its
        OWN once aborted; the watchdog never cancels or re-runs it, so the turn runs
        EXACTLY ONCE (the timeout is FINAL — an aborted turn is never retried, §3.11).
        """
        async with self._sem:  # the [3–5] bound on concurrent wake turns
            started = self._clock()
            self.wake_turns_run += 1
            digest = self._state_digest()
            watchdog = self._start_timeout_watchdog(event, started)
            try:
                await self._wake_turn(event, digest)
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await watchdog
                if self._clock() - started > self._detach_after_s:
                    record.detached = True

    def _start_timeout_watchdog(
        self, event: MeetingEvent, started: float
    ) -> "asyncio.Task[None] | None":
        """Arm the per-task hard-timeout WATCHDOG for a wake (§3.11), or None if disabled.

        The watchdog fires the wake's controller abort once the ``wake_timeout_s`` bound
        elapses **on the loop's injected clock** — it POLLS ``clock()`` (yielding a real
        event-loop tick each poll) rather than sleeping the whole bound, so the boundary is
        measured against the SAME clock the loop uses for detach. That makes the abort
        latency deterministic under a mocked clock (the boundary-value oracle for
        AC-CTRL-014/-014-B: answer in [4s,5s], Scribe in [3s,4s]) and, on the live monotonic
        clock, fires the abort within one poll of the true bound.

        It records the MEASURED elapsed at abort on ``event.abort_fired_after_ms`` (the
        latency the oracle reads) and does NOT cancel the turn's task — a cooperative turn
        unwinds on its own (the model loop polls ``.aborted``) and runs exactly once.
        Cancelled the instant the turn returns under the bound (the fast path never fires)."""
        if not (self._wake_timeout_s and self._wake_timeout_s > 0) or event.abort is None:
            return None

        bound = self._wake_timeout_s

        async def _fire() -> None:
            # Fire the abort once the hard bound elapses on the INJECTED clock — the same
            # clock the loop measures detach against — so the boundary is deterministic
            # under a mocked clock (the [4s,5s] answer / [3s,4s] Scribe window oracle).
            #
            # Cadence: yield tightly (``sleep(0)``) so the watchdog re-checks right after
            # each cooperative turn tick — a turn advancing a MOCKED clock crosses the bound
            # with overshoot bounded by the turn's OWN step, not the poll cadence. That tight
            # yield only spins while the injected clock is running AHEAD of wall time (a
            # virtual clock, which crosses the bound in milliseconds); on the LIVE monotonic
            # clock, where the injected clock tracks wall time, the watchdog instead sleeps a
            # real ``_WATCHDOG_POLL_S`` each tick (≈bound/poll wakeups, never a CPU spin) and
            # fires within one poll of the true bound.
            while self._clock() - started < bound:
                clock_before = self._clock()
                real_before = time.monotonic()
                await asyncio.sleep(0)
                injected_delta = self._clock() - clock_before
                real_delta = time.monotonic() - real_before
                # If the injected clock ran well AHEAD of wall time across that bare yield, a
                # cooperative turn is driving a virtual clock → keep yielding tightly so we
                # catch the boundary crossing with turn-step overshoot (the mocked-clock
                # oracle path). If the injected clock merely TRACKED wall time, the turn is
                # blocked on real I/O (the live path) → back off to a real poll so the
                # watchdog never CPU-spins for the whole bound.
                if injected_delta <= real_delta + _WATCHDOG_POLL_S:
                    remaining = bound - (self._clock() - started)
                    if remaining > 0:
                        await asyncio.sleep(min(_WATCHDOG_POLL_S, remaining))
            elapsed = self._clock() - started
            # The hard bound elapsed: fire the abort so the model loop halts (final), and
            # record the MEASURED elapsed (ms) so the latency-window oracle can read it.
            if event.abort is not None:
                event.abort_fired_after_ms = elapsed * 1000.0
                self._record_abort_latency(event.ask_id, event.abort_fired_after_ms)
                event.abort.abort()

        return asyncio.ensure_future(_fire())

    def _record_abort_latency(self, ask_id: str | None, fired_after_ms: float) -> None:
        """Stash a wake's hard-timeout abort latency (ms) so it outlives the turn (§3.11).

        The wake's controller retires from the registry the instant its turn completes, so
        the boundary-value oracle cannot read the latency off a live controller after the
        fact. Recording it here (keyed by ask) lets :meth:`last_abort_fired_after_ms`
        surface WHEN the abort fired for reconcile/telemetry and the acceptance oracle."""
        if ask_id is not None:
            self._abort_latency_ms[ask_id] = fired_after_ms

    def last_abort_fired_after_ms(self, ask_id: str) -> float | None:
        """The measured elapsed (ms) at which the hard timeout aborted this ask, or None.

        None if the ask never hit its per-task timeout (finished under the bound) or is
        unknown. Read off the injected clock, so an answer wake reads in [4000, 5000] and a
        Scribe wake in [3000, 4000] — the AC-CTRL-014/-014-B latency-window oracle (§3.11)."""
        return self._abort_latency_ms.get(ask_id)

    def _record_detached(self, ask_id: str, record: _InFlight) -> None:
        """Record an ask as detached (``was_detached`` True) — no re-wake here (§3.15).

        Marks the ask detached the instant the threshold passes; the re-wake is a
        SEPARATE event tied to the turn's completion (:meth:`_rewake`), so a genuinely
        long turn is recorded detached now but only re-wakes Proxy when it ends.
        """
        record.detached = True
        self._detached[ask_id] = record

    def _rewake(self, ask_id: str) -> None:
        """Fire the completion re-wake for a detached ask exactly once (§3.15)."""
        if self._on_rewake is not None:
            self._on_rewake(ask_id)

    def _on_turn_complete(self, ask_id: str | None, turn: asyncio.Task[None]) -> None:
        """A detached turn finished: drop it from in-flight, re-wake, surface errors.

        This is the *completion* the DoD ties the re-wake to: a detached turn that was
        genuinely still running is finished now, so its completion re-wakes Proxy (§3.15).
        Also clears the in-flight slot bookkeeping and re-raises a failed turn so it is
        never silently swallowed (a cancelled turn is expected on teardown).
        """
        if ask_id is not None:
            self._in_flight.pop(ask_id, None)
            self._task_keys.pop(ask_id, None)  # the finished turn's controller retires
            self._rewake(ask_id)
        if not turn.cancelled():
            turn.result()  # re-raise a genuine failure (never swallow it)

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
    "DEFAULT_WAKE_TIMEOUT_S",
    "MeetingEvent",
    "RunLoop",
    "StandingPipe",
]
