"""Doc 04 · orchestrator.run-loop — the DETACH property, bound so it can FAIL on regression.

The sealed ``test_run_loop_ask_past_two_seconds_detaches`` asserts only post-hoc
bookkeeping (``was_detached`` / ``on_rewake`` fired). It would still pass if the
loop blocked on the whole turn before recording anything — so it cannot catch the
"loop blocks on a long turn" regression the detach exists to prevent (§3.15 / §4
"detach threshold [~2s]"; the DoD: "an ask past ~2s detaches into a background task
whose completion re-wakes Proxy, so the loop never blocks on a long turn").

This suite binds the REAL detach behavior that bookkeeping alone cannot:

  * ``route()`` returns control BEFORE the wake turn finishes — the turn is still
    awaiting a gate when ``route`` has already resolved. A blocking loop fails here.
  * the re-wake (``on_rewake``) fires only at the turn's LATER completion, not
    inside the enclosing ``route`` call — proving completion re-wakes Proxy.
  * a detached turn keeps its ``[3–5]`` semaphore slot while it runs, but the loop
    does not — a second ask can be routed while the first is still in flight
    detached, because ``route`` no longer blocks on it.

These use a REAL wall-clock detach threshold (small, so the test is fast) and a
real ``asyncio.Event`` gate the turn awaits — the turn genuinely outlives the
``route`` call, which a post-hoc-only implementation cannot satisfy.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.integration
def test_route_returns_before_a_long_turn_finishes_and_rewakes_on_completion() -> None:
    """route() resolves while the wake turn is STILL awaiting a gate; completion re-wakes.

    A blocking loop (await the turn to completion inside route) can NEVER satisfy
    this: route only returns after the gate opens, so ``route_done_before_turn``
    would be False and ``rewoke`` would be non-empty at the assert point. The real
    detach returns control at ~threshold with the turn still running, then re-wakes
    when the background turn finally completes.
    """
    from harness.run_loop import MeetingEvent, RunLoop

    rewoke: list[str] = []

    async def _run() -> None:
        gate = asyncio.Event()
        turn_finished = asyncio.Event()

        async def _slow_wake(event: MeetingEvent, digest: dict) -> None:
            # The turn blocks on a gate the test opens only AFTER route() has
            # already returned — so the turn provably outlives the route call.
            await gate.wait()
            turn_finished.set()

        loop = RunLoop(
            wake_turn=_slow_wake,
            addressed=lambda e: True,
            max_in_flight=5,
            detach_after_s=0.05,  # real wall-clock threshold, kept small for speed
            on_rewake=lambda task_id: rewoke.append(task_id),
        )

        ev = MeetingEvent(payload="build the whole feature", ask_id="big-task")

        # route() must return once the detach threshold passes — WITHOUT the gate
        # ever being opened. If the loop blocked on the turn, this await would hang
        # until the (never-set) gate opened → the wait_for below would time out.
        result = await asyncio.wait_for(loop.route(ev), timeout=1.0)
        assert result is True, "an addressed ask returns True (a wake turn ran / detached)"

        # The turn is STILL running (its gate is still shut) at the moment route
        # returned — the loop did not block on it.
        assert not turn_finished.is_set(), (
            "route() must return BEFORE the long turn finishes — the turn is still "
            "awaiting its gate, so the loop did not block on it"
        )
        # And the ask is recorded detached the instant control returns.
        assert loop.was_detached("big-task") is True, "an ask past ~2s is recorded DETACHED"
        # Re-wake has NOT fired yet — completion is what re-wakes, and the turn is
        # not done. (A blocking loop would already have fired it here.)
        assert rewoke == [], (
            "the re-wake fires on the turn's COMPLETION, not inside route() — the "
            "turn is still running, so it must not have re-woken yet"
        )

        # Now let the detached background turn complete → its completion re-wakes.
        gate.set()
        for _ in range(200):
            if rewoke:
                break
            await asyncio.sleep(0)
        await turn_finished.wait()

        assert rewoke == ["big-task"], (
            "the detached turn's COMPLETION (a LATER point than the route call) "
            "must re-wake Proxy exactly once"
        )

    asyncio.run(_run())


@pytest.mark.integration
def test_loop_does_not_block_on_a_detached_turn_a_second_ask_routes_meanwhile() -> None:
    """While a long turn is detached and still running, the loop routes a SECOND ask.

    This is the concrete "the loop never blocks on a long turn" invariant: because
    route() returned control after the first ask detached, a second, distinct ask
    flows THROUGH the loop and its (fast) turn completes — all while the first turn
    is still awaiting its gate. A blocking loop would be stuck on the first turn and
    never reach the second.
    """
    from harness.run_loop import MeetingEvent, RunLoop

    fast_turns: list[str] = []

    async def _run() -> None:
        slow_gate = asyncio.Event()

        async def _wake(event: MeetingEvent, digest: dict) -> None:
            if event.ask_id == "slow":
                await slow_gate.wait()  # the long turn — never opens before route returns
            else:
                fast_turns.append(event.ask_id or "")  # a fast turn completes at once

        loop = RunLoop(
            wake_turn=_wake,
            addressed=lambda e: True,
            max_in_flight=5,
            detach_after_s=0.05,
        )

        # First ask detaches (its gate never opens within the threshold).
        first = await asyncio.wait_for(
            loop.route(MeetingEvent(payload="slow one", ask_id="slow")), timeout=1.0
        )
        assert first is True
        assert loop.was_detached("slow") is True, "the long first ask detached"

        # ...and the loop is FREE: a second, distinct ask routes to completion while
        # the first turn is still blocked on its gate.
        second = await asyncio.wait_for(
            loop.route(MeetingEvent(payload="quick one", ask_id="quick")), timeout=1.0
        )
        assert second is True
        assert fast_turns == ["quick"], (
            "a second ask must route to completion while the first is detached — the "
            "loop is not blocked on the long turn"
        )

        slow_gate.set()  # let the detached turn finish so no task leaks

    asyncio.run(_run())
