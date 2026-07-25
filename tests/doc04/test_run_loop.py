"""Doc 04 · orchestrator.run-loop — the asyncio event queue that IS the spine (§3/§3.2/§3.15/§3.17).

The RUN block of §3's build diagram, realized: ONE per-meeting ``asyncio.Queue``
onto which every signal lands (webhook, heartbeat, boundary, confirmed ask,
workroom-done, control command, cost threshold, anomaly), a loop that pulls each
event and routes it THROUGH a wake turn (or a reflex) — never an ``if event_type
→ handler`` dispatch table (that would violate Law 4, dynamic-not-hard-coded).
The standing pipes (audio→STT→transcript; transcript→Scribe; Scribe→judge;
heartbeats) are wired once at join as PURE forwarding.

The acceptance boundary this suite pins (the node's ``definition_of_done``):

  * ``event_queue`` — every event lands on ONE ``asyncio.Queue``; the loop pulls
    each and routes it to a wake turn or a reflex. There is NO per-event-type
    branch in the loop body: the ONLY code decision is the mechanical name-gate
    front-gate ("was Proxy addressed?"); an addressed event → the ONE generic
    wake turn, an un-addressed one → folded into state, no agent call.
  * ``standing_pipes`` — the pipes forward continuously with ZERO agent
    involvement: an hour-long (simulated clock) silent meeting keeps the pipes
    flowing and makes ZERO wake-turn/model calls when nothing is addressed.
  * ``run_loop`` in-flight bookkeeping (§3.15): a [3–5] semaphore bounds in-flight
    wake turns · a duplicate of an in-flight ask ATTACHES rather than spawning ·
    a correction mid-task INJECTS into that task's session · an ask past ~2s
    DETACHES · every mouth/apply/dispatch side-effect is gated on ``is_owner``
    (§3.7 fencing) — a fenced-out harness (is_owner False) never emits.

Product imports live inside the test bodies so this module COLLECTS red-first
(before the product exists). No DB is needed: the run loop is the in-process
asyncio spine (Postgres rows + wall-clock + the asyncio Task are the whole
runtime — no bus, no broker), so these are pure-asyncio integration tests over
the real loop with injected seams (a fake clock, a recording emitter, a
counting wake turn).
"""
from __future__ import annotations

import asyncio

import pytest


# ── event_queue: ONE queue; the loop routes every event through the SAME entry ──
@pytest.mark.integration
def test_event_queue_is_single_and_routes_addressed_to_wake_unaddressed_to_state() -> None:
    """Every event lands on ONE asyncio.Queue; addressed → wake turn, un-addressed → state (no agent)."""
    from harness.run_loop import MeetingEvent, RunLoop

    wake_calls: list[str] = []

    async def _fake_wake(event: MeetingEvent, digest: dict) -> None:
        # The ONE generic wake turn — the harness hands it "what happened + state".
        wake_calls.append(str(event.payload))

    # ``addressed`` is the mechanical front-gate verdict (the name-gate produces it);
    # the loop NEVER inspects the payload's TYPE to decide the action.
    def _addressed(event: MeetingEvent) -> bool:
        return getattr(event.payload, "wake", False) is True

    async def _run() -> None:
        loop = RunLoop(wake_turn=_fake_wake, addressed=_addressed)

        class _Addr:
            wake = True

        class _Plain:
            wake = False

        # ONE queue accepts every kind of event object with no per-type code.
        assert isinstance(loop.queue, asyncio.Queue), "the loop owns ONE asyncio.Queue"
        await loop.enqueue(MeetingEvent(payload=_Plain()))   # ambient signal — no wake
        await loop.enqueue(MeetingEvent(payload=_Addr()))    # confirmed ask — wake
        await loop.enqueue(MeetingEvent(payload=_Plain()))   # more ambient — no wake

        # Drain exactly the three enqueued events through the loop, then stop.
        await loop.run_until_idle()

        # Exactly ONE wake turn ran — the addressed event; the two ambient events
        # were folded into state with no agent call. The routing is the front-gate
        # verdict, not a dispatch table.
        assert len(wake_calls) == 1, (
            f"only the addressed event wakes Proxy; got {len(wake_calls)} wakes"
        )
        assert loop.events_routed == 3, "all three events flowed THROUGH the one loop"

    asyncio.run(_run())


def test_run_loop_has_no_event_type_dispatch_table() -> None:
    """Static guard: the loop body must not branch on the event's TYPE (Law 4).

    The routing IS the wake turn's judgment. A ``if isinstance(event, X)`` /
    ``event.type == "..."`` ladder over signal kinds in the loop body would be the
    hard-coded dispatch table the node's ``definition_of_done`` forbids.
    """
    import inspect

    from harness import run_loop

    src = inspect.getsource(run_loop.RunLoop)
    low = src.lower()
    # No per-signal-name dispatch ladder (the spec's forbidden shape).
    for banned in ("transcript", "boundary", "workroom_done", "cost_threshold", "anomaly"):
        assert f'== "{banned}"' not in low and f"== '{banned}'" not in low, (
            f"the loop must not branch on event type {banned!r} — routing is the wake turn"
        )


# ── standing_pipes: an hour of silence → pipes flow, ZERO agent calls ───────────
@pytest.mark.integration
def test_standing_pipes_forward_an_hour_of_silence_with_zero_agent_calls() -> None:
    """A test meeting runs an hour (simulated clock) with pipes flowing and ZERO wake turns."""
    from harness.run_loop import MeetingEvent, RunLoop, StandingPipe

    wake_calls: list[object] = []

    async def _fake_wake(event: MeetingEvent, digest: dict) -> None:
        wake_calls.append(event)

    # A silent hour: heartbeats + ambient transcript/boundary signals flow the pipes;
    # NOTHING is addressed to Proxy, so the front-gate never fires a wake.
    def _never_addressed(event: MeetingEvent) -> bool:
        return False

    forwarded: list[object] = []

    async def _run() -> None:
        loop = RunLoop(wake_turn=_fake_wake, addressed=_never_addressed)

        # A standing pipe is PURE forwarding: source → sink, no decision, no agent.
        # (audio→STT→transcript / transcript→Scribe / heartbeats — the shape is one.)
        async def _source():
            # One "hour" of heartbeats at 10s cadence on a simulated clock = 360 ticks.
            for i in range(360):
                yield {"heartbeat": i}

        async def _sink(item) -> None:
            forwarded.append(item)

        pipe = StandingPipe(source=_source(), sink=_sink)
        loop.wire_pipe(pipe)

        # Run the pipes to completion; the loop's event queue stays empty (nothing
        # addressed lands), so the loop makes zero wake turns.
        await loop.run_pipes_until_done()

        assert len(forwarded) == 360, (
            f"the standing pipe must forward every heartbeat; got {len(forwarded)}"
        )
        assert wake_calls == [], (
            f"a silent hour must make ZERO agent/wake calls; got {len(wake_calls)}"
        )
        assert loop.wake_turns_run == 0, "no wake turn runs when nothing is addressed"

    asyncio.run(_run())


def test_standing_pipe_is_pure_forwarding_no_decision() -> None:
    """A StandingPipe forwards its source verbatim to its sink — no judgment, no branch."""
    from harness.run_loop import StandingPipe

    seen: list[int] = []

    async def _run() -> None:
        async def _src():
            for n in (1, 2, 3):
                yield n

        async def _sink(x: int) -> None:
            seen.append(x)

        pipe = StandingPipe(source=_src(), sink=_sink)
        await pipe.run()

    asyncio.run(_run())
    assert seen == [1, 2, 3], "pure forwarding delivers the source unchanged to the sink"


# ── in-flight bookkeeping (§3.15): semaphore · attach · inject · detach ─────────
@pytest.mark.integration
def test_run_loop_semaphore_bounds_in_flight_wake_turns() -> None:
    """A [3–5] semaphore caps concurrent wake turns; extra asks queue behind it."""
    from harness.run_loop import MeetingEvent, RunLoop

    limit_seen = 0
    concurrent = 0

    async def _run() -> None:
        nonlocal limit_seen, concurrent
        gate = asyncio.Event()

        async def _slow_wake(event: MeetingEvent, digest: dict) -> None:
            nonlocal concurrent, limit_seen
            concurrent += 1
            limit_seen = max(limit_seen, concurrent)
            await gate.wait()  # hold the turn so concurrency piles up against the cap
            concurrent -= 1

        loop = RunLoop(
            wake_turn=_slow_wake,
            addressed=lambda e: True,
            max_in_flight=3,
        )
        assert 3 <= loop.max_in_flight <= 5, "the semaphore bound must be in [3–5] (§3.15)"

        # Fire more asks than the cap; each is a DISTINCT ask id so none attaches.
        tasks = []
        for i in range(6):
            ev = MeetingEvent(payload=_Ask(f"ask-{i}"), ask_id=f"ask-{i}")
            tasks.append(asyncio.create_task(loop.route(ev)))
        await asyncio.sleep(0.05)  # let the first wave grab the semaphore

        assert limit_seen <= 3, (
            f"never more than 3 wake turns in flight at once; saw {limit_seen}"
        )
        gate.set()  # release; the queued asks drain through
        await asyncio.gather(*tasks)

    asyncio.run(_run())


@pytest.mark.integration
def test_run_loop_duplicate_ask_attaches_not_spawns() -> None:
    """A duplicate of an in-flight ask ATTACHES to it rather than spawning a second turn (§3.15)."""
    from harness.run_loop import MeetingEvent, RunLoop

    spawned = 0

    async def _run() -> None:
        nonlocal spawned
        release = asyncio.Event()

        async def _wake(event: MeetingEvent, digest: dict) -> None:
            nonlocal spawned
            spawned += 1
            await release.wait()

        loop = RunLoop(wake_turn=_wake, addressed=lambda e: True, max_in_flight=5)

        same = "would-renaming-chargeCard-break-anything"
        t1 = asyncio.create_task(loop.route(MeetingEvent(payload=_Ask(same), ask_id=same)))
        await asyncio.sleep(0.02)  # t1 is now in flight, holding the ask id
        # The identical ask arrives again while the first is still running.
        attached = await loop.route(MeetingEvent(payload=_Ask(same), ask_id=same))
        assert attached is True, "a duplicate of an in-flight ask must ATTACH (return attached)"
        assert spawned == 1, f"the duplicate must NOT spawn a second wake turn; spawned={spawned}"

        release.set()
        await t1

    asyncio.run(_run())


@pytest.mark.integration
def test_run_loop_correction_injects_into_the_in_flight_task_session() -> None:
    """A correction mid-task INJECTS into that task's session rather than opening a new turn (§3.15)."""
    from harness.run_loop import MeetingEvent, RunLoop

    async def _run() -> None:
        release = asyncio.Event()
        injected: list[str] = []

        async def _wake(event: MeetingEvent, digest: dict) -> None:
            # Expose the injection channel the loop feeds a correction into.
            event.on_inject(lambda text: injected.append(text))
            await release.wait()

        loop = RunLoop(wake_turn=_wake, addressed=lambda e: True, max_in_flight=5)

        ask_id = "trace-the-impact"
        t1 = asyncio.create_task(loop.route(MeetingEvent(payload=_Ask("trace it"), ask_id=ask_id)))
        await asyncio.sleep(0.02)

        # A correction targeting the SAME in-flight task injects into its session.
        did_inject = await loop.inject_correction(ask_id, "actually, only the payments module")
        assert did_inject is True, "a correction on an in-flight task must inject"
        assert injected == ["actually, only the payments module"], (
            "the correction text must reach the in-flight task's session, not a new turn"
        )
        release.set()
        await t1

    asyncio.run(_run())


@pytest.mark.integration
def test_run_loop_ask_past_two_seconds_detaches() -> None:
    """Anything past ~2s DETACHES into a background task whose completion re-wakes Proxy (§3.15)."""
    from harness.run_loop import MeetingEvent, RunLoop

    rewoke: list[str] = []
    now = {"t": 0.0}

    def _clock() -> float:
        return now["t"]

    async def _run() -> None:
        async def _wake(event: MeetingEvent, digest: dict) -> None:
            # Simulate the turn taking > 2s on the injected clock; the loop must
            # DETACH it (return control) and re-wake on completion.
            now["t"] += 3.0

        loop = RunLoop(
            wake_turn=_wake,
            addressed=lambda e: True,
            max_in_flight=5,
            clock=_clock,
            detach_after_s=2.0,
            on_rewake=lambda task_id: rewoke.append(task_id),
        )

        ev = MeetingEvent(payload=_Ask("build the feature"), ask_id="big-task")
        result = await loop.route(ev)
        assert result is True
        # The turn ran > detach threshold → it detached and its completion re-woke Proxy.
        assert loop.was_detached("big-task") is True, (
            "an ask past ~2s must be recorded as DETACHED"
        )
        assert "big-task" in rewoke, "a detached task's completion must re-wake Proxy"

    asyncio.run(_run())


# ── fencing: every side-effect gated on is_owner — a fenced-out harness is silent ─
@pytest.mark.integration
def test_run_loop_side_effects_gated_on_is_owner_fencing() -> None:
    """Every mouth/apply/dispatch side-effect the loop makes is gated on is_owner (§3.7)."""
    from harness.emit import Emitter
    from harness.run_loop import MeetingEvent, RunLoop

    class _Handle:
        is_owner = True

    handle = _Handle()
    wire: list[tuple[str, object]] = []
    emitter = Emitter(handle=handle, sink=lambda v, p: wire.append((v, p)))

    async def _run() -> None:
        async def _wake(event: MeetingEvent, digest: dict) -> None:
            # The wake turn speaks through the gated emitter (the sole delivery seam).
            event.emitter.speak("Renaming chargeCard touches 14 call sites.")

        loop = RunLoop(wake_turn=_wake, addressed=lambda e: True, emitter=emitter, max_in_flight=5)

        # Owner: the side-effect reaches the wire.
        await loop.route(MeetingEvent(payload=_Ask("q1"), ask_id="q1"))
        assert wire == [("speak", "Renaming chargeCard touches 14 call sites.")], (
            "an owner's wake-turn side-effect must reach the wire"
        )

        # Fenced out (reclaimed row → is_owner False): the very next side-effect is silenced.
        handle.is_owner = False
        wire.clear()
        await loop.route(MeetingEvent(payload=_Ask("q2"), ask_id="q2"))
        assert wire == [], (
            "a fenced-out (is_owner False) harness must reach the wire ZERO times"
        )

    asyncio.run(_run())


# ── the loop is WIRED into the live per-meeting runtime (integration_point) ─────
@pytest.mark.integration
def test_meeting_runtime_owns_a_run_loop_fed_by_the_carrier() -> None:
    """MeetingRuntime wires a RunLoop whose standing pipe forwards carrier signals (§3.2).

    The node's ``integration_point``: ``run_loop.py`` wired into
    ``meeting_runtime.py`` (the per-meeting assembly). The runtime owns ONE
    :class:`RunLoop`; a standing pipe subscribes to the meeting's ``SignalCarrier``
    and lands every emitted signal on the loop's ONE queue as a ``MeetingEvent``.
    An addressed transcript wakes Proxy; an ambient one is folded into state — with
    the routing being the front-gate verdict, never a per-signal-type branch.
    """
    from scribe.pipeline import HostBudget
    from scribe.prefix import MeetingHeader
    from transport.carrier import SignalCarrier
    from transport.signals import Boundary, Transcript

    from harness.meeting_runtime import MeetingRuntime

    woke: list[str] = []

    async def _wake(event, digest) -> None:
        woke.append(getattr(event.payload, "words", str(event.payload)))

    # The front gate wakes only on the literal name "proxy" as a word (the
    # mechanical name-gate's own physics) — an ambient line stays ambient.
    def _addressed(event) -> bool:
        words = getattr(event.payload, "words", "")
        return isinstance(words, str) and "proxy" in words.lower()

    async def _run() -> None:
        carrier = SignalCarrier()
        header = MeetingHeader(meeting_id="m-runloop")
        runtime = MeetingRuntime(
            header=header, carrier=carrier, db=None, host_budget=HostBudget(limit=8)
        )
        loop = runtime.build_run_loop(wake_turn=_wake, addressed=_addressed)
        assert runtime.run_loop is loop, "the runtime must own its RunLoop"
        assert loop.queue is not None, "the RunLoop owns THE per-meeting event queue"

        # Wire the transport→orchestrator standing pipe SYNCHRONOUSLY at join (the
        # subscriber registers before any signal is emitted), then run it, then emit
        # an ambient boundary + an ambient line + an addressed ask.
        pipe = runtime.wire_orchestrator_pipe()
        pump = asyncio.create_task(pipe.run())
        await carrier.emit(Boundary(t=0.0))
        await carrier.emit(Transcript(words="let's ship the payments refactor", speaker="sam", t=1.0))
        await carrier.emit(Transcript(words="proxy, would renaming chargeCard break anything?", speaker="sam", t=2.0))
        # Let the pipe forward + route the three signals.
        for _ in range(50):
            await asyncio.sleep(0)
            if loop.events_routed >= 3:
                break
        carrier.close()
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

        assert loop.events_routed >= 3, "every emitted signal flowed THROUGH the one loop"
        assert woke == ["proxy, would renaming chargeCard break anything?"], (
            f"only the addressed ask wakes Proxy; got {woke}"
        )

    asyncio.run(_run())


# ── a tiny stand-in ask payload (structurally the confirmed-address shape) ──────
class _Ask:
    """A minimal addressed-ask payload — carries the ask text + the wake flag."""

    wake = True

    def __init__(self, text: str) -> None:
        self.text = text
        self.speaker = "tester"

    def __str__(self) -> str:  # what the fake wake turn records
        return self.text
