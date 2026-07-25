"""Acceptance — the abort registry WIRED into the live loop (04 §3.11, CANONICAL §11.9).

``test_abort_discipline.py`` proves the abort PRIMITIVE in isolation (make/cancel/
cancel_meeting/wait). This suite proves the primitive is actually THREADED into the
live run loop, the meeting-end close, and the barge-in path — the four P0/P1
acceptance criteria a fresh adversarial verifier found UNMET:

  * **AC-CTRL-017 (P0).** ``RunLoop`` mints a real ``registry.make(meeting_id|ask_id)``
    controller for each wake and threads it into the wake turn (``event.abort`` is the
    live controller, NOT a bare ``_Abort()`` that never fires). A new ask for the same
    key preempts the stale one via ``make()``.
  * **AC-CTRL-012 (P0).** ``MeetingRuntimeRegistry.end_meeting`` calls
    ``registry.cancel_meeting(meeting_id)`` so every in-flight model loop for the
    meeting is aborted at close (a controller made before ``end_meeting`` is aborted).
  * **AC-CTRL-013 (P0).** The "Proxy, quiet" / barge-in path cancels the addressed
    in-flight model-loop task via ``registry.cancel(task_id)`` — the model loop halts —
    AND the TTS speech cut still fires (the sub-200ms path is not replaced).
  * **AC-CTRL-014 (P1).** A per-task hard timeout wraps the wake turn and fires the
    controller's abort once the ~4s (answer) / ~3–4s (scribe) bound is exceeded.

The registry is IMPORTED from ``libs/agentkit/abort.py`` — never redefined (§11.9).
"""
from __future__ import annotations

import asyncio

import pytest

from libs.agentkit import AbortController, AbortRegistry


# ---------------------------------------------------------------------------
# AC-CTRL-017 — RunLoop threads a real make()'d controller into the wake turn
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_loop_threads_a_real_controller_into_the_wake_turn() -> None:
    """The wake turn receives the registry's OWN live controller, not a bare _Abort().

    Proves the loop mints ``registry.make(meeting_id|ask_id)`` and hands THAT handle to
    the turn (``event.abort``). The bare ``_Abort()`` fallback in ``wake_turn.py`` would
    never fire on cancel — this test fails if the loop leaves ``event.abort`` None.
    """
    from harness.run_loop import MeetingEvent, RunLoop

    reg = AbortRegistry()
    seen: list[AbortController] = []

    async def _wake(event: MeetingEvent, digest: dict) -> None:
        # The wake turn is handed the LIVE controller for this ask on the event.
        assert event.abort is not None, "the loop must thread a real controller onto the event"
        seen.append(event.abort)

    loop = RunLoop(
        wake_turn=_wake,
        addressed=lambda _e: True,
        registry=reg,
        meeting_id="m1",
    )
    await loop.route(MeetingEvent(payload="where is chargeCard?", ask_id="ask-1"))

    assert len(seen) == 1
    controller = seen[0]
    # It is the registry's own controller keyed meeting_id|ask_id — cancelling that key
    # aborts the very handle the turn saw (the loop threaded the real one, not a copy).
    assert isinstance(controller, AbortController)
    reg.cancel("m1|ask-1")
    assert controller.aborted is True, "cancelling the registry key must abort the threaded controller"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_new_ask_for_the_same_key_preempts_the_prior_via_make() -> None:
    """A fresh judgment-moment for the same ask preempts the stale one (make() semantics).

    Two wakes for the SAME meeting_id|ask_id: the first controller is aborted the instant
    the loop mints the second (stale-judgment preemption), proving the loop routes through
    ``registry.make`` (which cancels the prior) rather than a bare per-turn handle.
    """
    from harness.run_loop import MeetingEvent, RunLoop

    reg = AbortRegistry()
    seen: list[AbortController] = []

    async def _wake(event: MeetingEvent, digest: dict) -> None:
        seen.append(event.abort)

    loop = RunLoop(wake_turn=_wake, addressed=lambda _e: True, registry=reg, meeting_id="m1")

    await loop.route(MeetingEvent(payload="q", ask_id="dup"))
    first = seen[0]
    assert first.aborted is False
    # A second, distinct judgment-moment for the SAME ask key preempts the first.
    await loop.route(MeetingEvent(payload="q again", ask_id="dup"), force_new=True)
    second = seen[1]
    assert first.aborted is True, "make() on a live key must abort the stale controller"
    assert second.aborted is False
    assert second is not first


# ---------------------------------------------------------------------------
# AC-CTRL-012 — meeting end aborts every live controller of the meeting
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meeting_end_cancels_every_live_controller_of_the_meeting() -> None:
    """``end_meeting`` → ``registry.cancel_meeting`` aborts all the meeting's controllers.

    A controller made (via the shared registry) BEFORE ``end_meeting`` is aborted by the
    close — an in-flight model loop never survives meeting end (AC-CTRL-012). A second
    meeting's controller is untouched (isolation).
    """
    from scribe.pipeline import HostBudget
    from scribe.prefix import MeetingHeader

    from harness.meeting_runtime import MeetingRuntime, MeetingRuntimeRegistry

    reg = AbortRegistry()
    # The registry shares the ONE abort registry (§11.9) so its end_meeting can
    # cancel every in-flight model-loop controller of a meeting.
    mreg = MeetingRuntimeRegistry(db=_FakeDB(), abort_registry=reg)

    class _FakeCarrier:
        def close(self) -> None:
            pass

    # Register runtimes directly (no live Scribe consumer needed for this wiring test):
    # end_meeting's cancel_meeting is a registry call, orthogonal to the close pass.
    for mid in ("mA", "mB"):
        header = MeetingHeader(meeting_id=mid, agenda=mid, participants=())
        runtime = MeetingRuntime(
            header=header, carrier=_FakeCarrier(), db=_FakeDB(), host_budget=HostBudget(limit=8)
        )
        mreg._runtimes[mid] = runtime

    # Live model-loop controllers for both meetings, minted through the SHARED registry.
    live_a = reg.make("mA|ask-1")
    live_a2 = reg.make("mA|ask-2")
    live_b = reg.make("mB|ask-1")

    await mreg.end_meeting("mA")

    assert live_a.aborted is True, "meeting end must abort every in-flight controller of the meeting"
    assert live_a2.aborted is True
    assert live_b.aborted is False, "another meeting's controllers are untouched (isolation)"


# ---------------------------------------------------------------------------
# AC-CTRL-013 — "Proxy, quiet" barge-in aborts the model loop AND cuts speech
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_quiet_barge_in_aborts_the_addressed_model_loop_and_still_cuts_tts() -> None:
    """"Proxy, quiet" cancels the addressed wake's controller AND fires the TTS cut.

    The barge-in path must ADD the model-loop cancel (``registry.cancel(task_id)``) WITHOUT
    dropping the sub-200ms speech cut. Proves both: the addressed in-flight controller is
    aborted (model loop halts) and the TTS abort still marks the utterance cancelled.
    """
    from transport.turn import OutputMediaSink, TTSProvider, TurnController

    reg = AbortRegistry()

    # An in-flight wake's controller for this meeting/ask, registered as the run loop would.
    task_key = "mA|ask-1"
    in_flight = reg.make(task_key)

    # A live TTS turn on the SAME registry (the transport barge-in set-membership seam).
    class _TTS(TTSProvider):
        async def synthesize(self, text: str):
            for _ in range(1000):
                yield b"x"
                await asyncio.sleep(0)

    class _Sink(OutputMediaSink):
        def __init__(self) -> None:
            self.flushed = 0

        async def write_audio(self, chunk: bytes) -> None:
            await asyncio.sleep(0)

        async def flush(self) -> None:
            self.flushed += 1

    controller = TurnController(_TTS(), _Sink(), abort=reg)
    controller.enqueue("a long answer")
    await controller.on_boundary()
    await asyncio.sleep(0)
    uid = controller._current_id
    assert uid is not None

    # "Proxy, quiet": cut speech AND cancel the addressed model loop.
    await controller.quiet(task_key)

    assert reg.is_aborted(uid) is True, "the TTS speech cut must still fire (<200ms path intact)"
    assert in_flight.aborted is True, "the addressed model-loop controller must be aborted"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_loop_cancel_ask_halts_the_addressed_in_flight_model_loop() -> None:
    """The run loop's ``cancel_ask`` (the harness-side "Proxy, quiet" entry) aborts the
    addressed ask's LIVE controller — the very handle the wake turn is running under.

    Binds the run loop's in-flight bookkeeping (ask_id → the registry key) to the model-
    loop kill: while a wake is genuinely in flight, ``cancel_ask(ask_id)`` fires its
    controller so the model loop halts (AC-CTRL-013, the harness side of "Proxy, quiet").
    """
    from harness.run_loop import MeetingEvent, RunLoop

    reg = AbortRegistry()
    seen: list[AbortController] = []
    released = asyncio.Event()

    async def _wake(event: MeetingEvent, digest: dict) -> None:
        seen.append(event.abort)
        # Stay in flight until cancelled (a long model loop polling .aborted).
        for _ in range(2000):
            if event.abort is not None and event.abort.aborted:
                released.set()
                return
            await asyncio.sleep(0.002)

    loop = RunLoop(
        wake_turn=_wake,
        addressed=lambda _e: True,
        registry=reg,
        meeting_id="mA",
        detach_after_s=0.02,  # detach quickly so the turn runs in the background
        wake_timeout_s=0.0,   # disable the timeout so ONLY cancel_ask ends this turn
    )
    # Route it; it detaches (still running) so control returns here with it in flight.
    await asyncio.wait_for(loop.route(MeetingEvent(payload="trace it", ask_id="live")), timeout=1.0)
    assert seen and seen[0].aborted is False, "the turn is in flight, not yet aborted"

    # "Proxy, quiet" on the addressed ask → the in-flight model loop is cancelled.
    assert loop.cancel_ask("live") is True, "cancel_ask must find + cancel the live controller"
    await asyncio.wait_for(released.wait(), timeout=1.0)
    assert seen[0].aborted is True, "the addressed in-flight model loop was halted"


# ---------------------------------------------------------------------------
# AC-CTRL-014 — a per-task hard timeout fires the controller's abort
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_wake_past_the_hard_timeout_has_its_controller_aborted() -> None:
    """A wake turn that runs past the per-task bound has its controller aborted (AC-CTRL-014).

    The loop wraps the wake in an ``asyncio`` timeout; a turn that overruns the (tiny, test-
    scaled) bound fires ``controller.abort()`` so a stalled meeting recovers — a stalled
    meeting is worse than a dropped note (§3.11).
    """
    from harness.run_loop import MeetingEvent, RunLoop

    reg = AbortRegistry()
    captured: list[AbortController] = []

    async def _slow_wake(event: MeetingEvent, digest: dict) -> None:
        captured.append(event.abort)
        # Runs well past the bound; a cooperative model loop polls event.abort.aborted.
        for _ in range(1000):
            if event.abort is not None and event.abort.aborted:
                return  # cooperative: the timeout fired the abort, so we stop
            await asyncio.sleep(0.005)

    loop = RunLoop(
        wake_turn=_slow_wake,
        addressed=lambda _e: True,
        registry=reg,
        meeting_id="mA",
        wake_timeout_s=0.05,
    )
    await asyncio.wait_for(loop.route(MeetingEvent(payload="stalls", ask_id="slow")), timeout=2.0)

    assert captured, "the wake ran"
    assert captured[0].aborted is True, "the per-task timeout must fire the controller's abort"


# ---------------------------------------------------------------------------
# Abort is FINAL — a timed-out turn is not retried/resurrected by the loop
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_timed_out_turn_is_not_retried_by_the_loop() -> None:
    """The loop runs a timed-out ask exactly once — abort is final, never resurrected."""
    from harness.run_loop import MeetingEvent, RunLoop

    reg = AbortRegistry()
    runs = 0

    async def _slow_wake(event: MeetingEvent, digest: dict) -> None:
        nonlocal runs
        runs += 1
        for _ in range(1000):
            if event.abort is not None and event.abort.aborted:
                return
            await asyncio.sleep(0.005)

    loop = RunLoop(
        wake_turn=_slow_wake,
        addressed=lambda _e: True,
        registry=reg,
        meeting_id="mA",
        wake_timeout_s=0.05,
    )
    await asyncio.wait_for(loop.route(MeetingEvent(payload="stalls", ask_id="slow")), timeout=2.0)
    assert runs == 1, "a timed-out turn is run once and never retried (abort is final)"


class _FakeDB:
    """A minimal stand-in for libs.db.Database — the runtime never touches it in these
    tests (no Scribe consumer is driven), it is only stashed on the registry at boot."""

    pass
