"""Acceptance — the REAL orchestrator brain wired onto the LIVE meeting path (04 §3.2/§3.11).

``test_abort_wiring.py`` proves the abort primitives thread into a ``RunLoop`` when the
wake turn + name-gate are injected DIRECTLY into ``RunLoop(...)``. But the PROVISIONER
assembled the runtime with ``build_run_loop()`` and NO args — so on the real meeting path
the loop ran with ``_noop_wake`` (Proxy never woke) and a never-addressed predicate (every
line folded to the digest), and the live ``AbortController`` the loop minted per wake was
threaded into a wake that did nothing. The brain was HOLLOW.

This suite assembles the runtime **the way the provisioner does** — through
:func:`harness.live_brain.assemble_live_brain` (the exact seam ``_assemble_runtime`` calls)
— but injects a FAKE ``AgentProvider`` (a recording stub; NO live Anthropic call). It
proves the LIVE seam, not the registry in isolation:

  * an ADDRESSED event routed through the assembled run loop runs a REAL WakeTurn (the fake
    provider RECEIVED a query — ``_noop_wake`` did NOT run) and the loop's minted
    ``event.abort`` controller reached the provider (AC-CTRL-017);
  * "Proxy, quiet" via the live trigger cancels the addressed in-flight model loop (the fake
    provider's stream observes ``.aborted`` True and breaks) AND the TTS cut still fires
    (AC-CTRL-013);
  * meeting-end teardown calls ``cancel_meeting`` → a controller made before end is aborted
    (AC-CTRL-012);
  * the per-task hard timeout fires ``event.abort`` on a wake that runs past the bound
    (AC-CTRL-014);
  * an un-addressed (silent) event runs ZERO wakes (the fake provider received no query).

The primitives (run loop, wake turn, abort registry, name-gate, turn controller) are
IMPORTED and wired — none is redefined here (§11.9).
"""
from __future__ import annotations

import asyncio

import pytest

from libs.agentkit import AbortController, AbortRegistry
from libs.contracts import AgentChunk


# ── the FAKE AgentProvider (a recording stub — NO live Anthropic call) ────────


class FakeProvider:
    """A recording ``AgentProvider`` stub satisfying the ``agentkit.Provider`` seam.

    ``stream(prompt, query)`` records that it was CALLED (the query it saw, and the live
    abort handle threaded onto ``query.abort``) and yields a canned ``AgentChunk`` stream.
    In the default mode it yields a ``speak`` TOOL_USE + RESULT and returns. In
    ``block_until_abort`` mode it loops while polling ``query.abort.aborted`` — so a
    "Proxy, quiet" / meeting-end / timeout that fires the controller is OBSERVED here (the
    provider breaks its SDK loop exactly as ``ClaudeAgentProvider.stream`` does), proving
    the abort reached the model loop, not merely the registry.
    """

    name = "claude"

    def __init__(self, *, said: str = "the retry logic is in checkout.py:42", block_until_abort: bool = False):
        self._said = said
        self._block = block_until_abort
        self.calls = 0
        self.seen_aborts: list = []
        self.seen_prompts: list[str] = []
        self.observed_abort = asyncio.Event()

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt, query):
        self.calls += 1
        self.seen_prompts.append(prompt)
        abort = getattr(query, "abort", None)
        self.seen_aborts.append(abort)
        said = self._said
        block = self._block
        observed = self.observed_abort

        async def gen():
            yield AgentChunk(type="INIT", metadata={"session_id": "sess-1"})
            if block:
                # A long model loop that polls the live abort handle and breaks on it —
                # exactly the ClaudeAgentProvider abort-break, so a quiet/end/timeout is
                # observed HERE (the abort reached the model loop, not just the registry).
                for _ in range(5000):
                    if abort is not None and getattr(abort, "aborted", False):
                        observed.set()
                        return
                    await asyncio.sleep(0.002)
                observed.set()
                return
            yield AgentChunk(
                type="TOOL_USE",
                metadata={"name": "speak", "input": {"text": said}, "id": "m1"},
            )
            yield AgentChunk(
                type="RESULT",
                metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": "sess-1"},
            )

        return gen()


# ── the runtime assembled the way the provisioner does (fake provider) ────────


def _carrier():
    """The real in-process ``SignalCarrier`` (no bus, no socket) — so the assembly wires
    its orchestrator pipe exactly as at join, and the seam under test is faithful."""
    from transport.carrier import SignalCarrier

    return SignalCarrier()


class _FakeDB:
    """A stand-in for libs.db.Database — the durable notes reader tolerates its absence
    (best-effort read → ``""``), so no live Postgres is needed for the seam proof."""

    def acquire(self):  # pragma: no cover - the notes read is best-effort here
        raise RuntimeError("no db in this seam test")


def _make_runtime(abort_registry: AbortRegistry):
    """Build a ``MeetingRuntime`` exactly as ``registry.start_meeting`` would, minus the
    live Scribe consumer (this seam test drives the run loop, not the notes engine)."""
    from scribe.pipeline import HostBudget
    from scribe.prefix import MeetingHeader

    from harness.meeting_runtime import MeetingRuntime

    header = MeetingHeader(meeting_id="mtg-1", agenda="standup", participants=("Sam",))
    return MeetingRuntime(
        header=header,
        carrier=_carrier(),
        db=_FakeDB(),
        host_budget=HostBudget(limit=8),
        abort_registry=abort_registry,
    )


def _assemble(runtime, provider, **kw):
    """Assemble the live brain the way ``provisioner._assemble_runtime`` does — the ONE seam
    under test. Wires the orchestrator pipe after, mirroring the provisioner's join order."""
    from harness.live_brain import assemble_live_brain

    brain = assemble_live_brain(runtime, provider=provider, **kw)
    runtime.live_brain = brain
    runtime.wire_orchestrator_pipe()
    return brain


def _transcript(words: str, speaker: str = "Sam"):
    from transport.signals import Transcript

    return Transcript(words=words, speaker=speaker, t=0.0)


# ── AC-CTRL-017 — an addressed event runs a REAL WakeTurn through the live loop ─


@pytest.mark.integration
@pytest.mark.asyncio
async def test_addressed_event_runs_a_real_wake_turn_not_noop() -> None:
    """An ADDRESSED line routed through the assembled loop runs a REAL WakeTurn (§3.2).

    The fake provider RECEIVED a query — proving ``_noop_wake`` did NOT run — and the live
    ``event.abort`` controller the loop minted reached the provider (``query.abort`` is the
    registry's own handle, AC-CTRL-017). The turn's spoken result flows through the gated
    emitter.
    """
    from harness.run_loop import MeetingEvent

    reg = AbortRegistry()
    runtime = _make_runtime(reg)
    provider = FakeProvider()
    _assemble(runtime, provider)

    # Route an addressed spoken line ("Proxy, ...") through the ASSEMBLED loop.
    loop = runtime.run_loop
    assert loop is not None
    await loop.route(MeetingEvent(payload=_transcript("Proxy, where is the retry logic?"), ask_id="ask-1"))

    assert provider.calls == 1, "the real WakeTurn ran (the fake provider received a query) — not _noop_wake"
    # The loop minted a live controller and threaded it to the provider (not a bare _Abort).
    seen = provider.seen_aborts[0]
    assert isinstance(seen, AbortController), "the loop's minted controller reached the provider seam"
    reg.cancel("mtg-1|ask-1")
    assert seen.aborted is True, "cancelling the registry key aborts the very handle the provider saw"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_addressed_event_speaks_through_the_gated_emitter() -> None:
    """The real WakeTurn's spoken result reaches the wire through the gated emitter (§3.7)."""
    from harness.run_loop import MeetingEvent

    reg = AbortRegistry()
    runtime = _make_runtime(reg)

    # Bind an owner emitter so the gated speak reaches the wire; drain it after.
    from harness.emit import Emitter

    class _Owner:
        is_owner = True

    emitter = Emitter(handle=_Owner())
    runtime.operation_handle = _Owner()
    provider = FakeProvider(said="it's in checkout.py:42")
    _assemble(runtime, provider)
    # The run loop was built by the assembly; point its emitter at our owner emitter.
    loop = runtime.run_loop
    assert loop is not None
    loop._emitter = emitter

    await loop.route(MeetingEvent(payload=_transcript("Proxy, where's the retry?"), ask_id="ask-1"))

    wire = emitter.drain_wire()
    assert ("speak", "it's in checkout.py:42") in wire, "the turn's spoken answer reached the gated wire"


# ── the silent-hour property — an un-addressed event runs ZERO wakes ──────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unaddressed_silent_event_runs_zero_wakes() -> None:
    """An un-addressed (no "Proxy") line folds to the digest with ZERO agent calls (§3.1).

    The name-gate is the live ``addressed`` predicate: a line that never names Proxy is
    un-addressed, so no wake runs and the fake provider is NEVER queried — the "silent hour
    = zero wakes" property, held TRUE for real (silence is un-addressed).
    """
    from harness.run_loop import MeetingEvent

    reg = AbortRegistry()
    runtime = _make_runtime(reg)
    provider = FakeProvider()
    _assemble(runtime, provider)

    loop = runtime.run_loop
    assert loop is not None
    # An ordinary line with no address to Proxy.
    routed = await loop.route(MeetingEvent(payload=_transcript("let's ship the migration Friday"), ask_id="a1"))

    assert routed is False, "an un-addressed line is ambient — folded to state, never a wake"
    assert provider.calls == 0, "a silent (un-addressed) event runs ZERO wakes — no agent call"
    assert loop.wake_turns_run == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_addressed_but_disambiguated_out_runs_zero_wakes() -> None:
    """A spoken name-hit the disambiguator rejects ("the proxy server") runs ZERO wakes."""
    from harness.run_loop import MeetingEvent

    reg = AbortRegistry()
    runtime = _make_runtime(reg)
    provider = FakeProvider()
    # The injected disambiguator says "not addressed" for this hit (a common-noun mention).
    _assemble(runtime, provider, disambiguate=lambda _line: False)

    loop = runtime.run_loop
    assert loop is not None
    routed = await loop.route(MeetingEvent(payload=_transcript("check the proxy server config"), ask_id="a1"))

    assert routed is False, "a rejected name-hit is not an address — no wake"
    assert provider.calls == 0


# ── AC-CTRL-013 — "Proxy, quiet" halts the addressed in-flight model loop + cuts TTS ─


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quiet_via_live_trigger_halts_the_in_flight_model_loop_and_cuts_tts() -> None:
    """"Proxy, quiet" via the LIVE trigger cancels the addressed in-flight model loop AND cuts TTS.

    The fake provider blocks while polling ``query.abort.aborted``; the run loop detaches it
    (still in flight). ``brain.quiet(ask_id)`` resolves the addressed ask's registry key from
    the run loop's ``_task_keys`` and cancels THAT controller — the provider's stream observes
    ``.aborted`` True and breaks (the model loop halts), and the TTS cut fired on the same
    shared registry (AC-CTRL-013). The sub-200ms speech cut is not replaced.
    """
    from transport.seams import OutputMediaSink, TTSProvider

    from harness.run_loop import MeetingEvent

    # A long-running TTS turn + sink so the quiet's sub-200ms speech cut is observable.
    class _LongTTS(TTSProvider):
        async def synthesize(self, text: str):
            for _ in range(1000):
                yield b"x"
                await asyncio.sleep(0)

    class _RecordingSink(OutputMediaSink):
        def __init__(self) -> None:
            self.flushed = 0

        async def write_audio(self, chunk: bytes) -> None:
            await asyncio.sleep(0)

        async def flush(self) -> None:
            self.flushed += 1

    reg = AbortRegistry()
    runtime = _make_runtime(reg)
    provider = FakeProvider(block_until_abort=True)
    sink = _RecordingSink()
    brain = _assemble(runtime, provider, tts=_LongTTS(), sink=sink)

    loop = runtime.run_loop
    assert loop is not None
    # Make the loop detach a long turn quickly and never self-timeout, so ONLY quiet ends it.
    loop._detach_after_s = 0.02
    loop._wake_timeout_s = 0.0

    # Enqueue a live TTS turn on the SHARED registry so the quiet's speech cut is observable.
    controller = brain.controller
    controller.enqueue("a long spoken answer")
    await controller.on_boundary()
    await asyncio.sleep(0)
    uid = controller._current_id
    assert uid is not None, "a TTS turn is speaking (the sub-200ms cut target)"

    # Route the addressed ask; it detaches (still running) with the provider blocked.
    await asyncio.wait_for(
        loop.route(MeetingEvent(payload=_transcript("Proxy, trace the refund path"), ask_id="ask-live")),
        timeout=1.0,
    )
    await asyncio.sleep(0)
    assert provider.calls == 1 and provider.observed_abort.is_set() is False, "the model loop is in flight"

    # "Proxy, quiet" via the LIVE brain trigger: halt the model loop AND cut speech.
    await brain.quiet("ask-live")

    await asyncio.wait_for(provider.observed_abort.wait(), timeout=1.0)
    assert provider.observed_abort.is_set() is True, "the addressed in-flight model loop was halted (provider broke its loop)"
    assert reg.is_aborted(uid) is True, "the sub-200ms TTS speech cut still fired (not replaced)"
    assert sink.flushed >= 1, "the Output-Media buffer was flushed on the cut (the speech cut is intact)"


# ── AC-CTRL-012 — meeting-end teardown cancels every in-flight controller ─────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meeting_end_teardown_cancels_in_flight_controllers() -> None:
    """Meeting-end teardown calls ``cancel_meeting`` → a controller made before end is aborted.

    The registry-level end path is what the ordered close runs FIRST (before
    freeze→close-pass→destroy→complete-row→teardown). A controller minted through the SHARED
    registry before ``end_meeting`` is aborted at close, and a sibling meeting's controller is
    untouched (isolation) — AC-CTRL-012.
    """
    from harness.meeting_runtime import MeetingRuntimeRegistry

    reg = AbortRegistry()
    mreg = MeetingRuntimeRegistry(db=_FakeDB(), abort_registry=reg)

    from scribe.pipeline import HostBudget
    from scribe.prefix import MeetingHeader

    from harness.meeting_runtime import MeetingRuntime

    for mid in ("mtg-1", "mtg-2"):
        header = MeetingHeader(meeting_id=mid, agenda=mid, participants=())
        rt = MeetingRuntime(
            header=header, carrier=_carrier(), db=_FakeDB(),
            host_budget=HostBudget(limit=8), abort_registry=reg,
        )
        mreg._runtimes[mid] = rt

    live = reg.make("mtg-1|ask-live")
    sibling = reg.make("mtg-2|ask-live")

    await mreg.end_meeting("mtg-1")

    assert live.aborted is True, "meeting-end teardown aborts the in-flight model-loop controller"
    assert sibling.aborted is False, "a sibling meeting's controller is untouched (isolation)"


# ── AC-CTRL-014 — the per-task hard timeout fires event.abort on an overrun ───


@pytest.mark.integration
@pytest.mark.asyncio
async def test_per_task_hard_timeout_fires_the_wake_abort() -> None:
    """A wake that runs past the per-task bound has its ``event.abort`` fired (AC-CTRL-014).

    The fake provider blocks on the live abort; the loop's per-task hard-timeout watchdog
    fires the controller once the (tiny, test-scaled) bound elapses — the provider observes
    ``.aborted`` True and breaks (a stalled meeting recovers; a stalled meeting is worse than
    a dropped note, §3.11).
    """
    from harness.run_loop import MeetingEvent

    reg = AbortRegistry()
    runtime = _make_runtime(reg)
    provider = FakeProvider(block_until_abort=True)
    _assemble(runtime, provider)

    loop = runtime.run_loop
    assert loop is not None
    loop._wake_timeout_s = 0.05  # a tiny, test-scaled per-task bound

    await asyncio.wait_for(
        loop.route(MeetingEvent(payload=_transcript("Proxy, stall on this one"), ask_id="slow")),
        timeout=2.0,
    )
    await asyncio.wait_for(provider.observed_abort.wait(), timeout=2.0)
    assert provider.observed_abort.is_set() is True, "the per-task hard timeout fired the wake's abort"
