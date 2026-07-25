"""Doc 02 · Milestone 7 — a MEASURED onset→stop barge-in oracle (AC-TURN-15).

The sealed AC-TURN-15 test (``test_m7_turn.py::test_provable_on_real_audio_placeholder``)
proves only that ``barge_in``/``hard_mute`` are *callable* — a placeholder, not a
behavioural proof. This file authors the real oracle it stood in for, driving the REAL
``TurnController``/``TurnSignalPump`` on the real event loop:

- A VAD onset frame (fed as a real :class:`~transport.turn.VadFrame`) cuts an in-flight
  TTS stream **mid-word within budget** — the cut latency is MEASURED on an injected
  monotonic clock (``onset → last-chunk-written``), and a *large buffered stretch*
  (hundreds of queued small chunks) is proven unable to defeat the cut (AC-TURN-07/10).
- Barge-in is **VAD-sourced**: it fires on a human onset frame, and NEVER on a
  Proxy-labelled frame or on a silence frame (AC-TURN-01/11).
- A boundary opens **only** on a real AAI ``end_of_turn`` message, never on a timer or a
  mid-thought breath (AC-TURN-02/05/06).
- Hard-mute silences voice while tile+chat stay live; speaking XOR muted (AC-TURN-12/14).

Budget: §3.6 mandates a human onset stops TTS in ``[<200ms]``. We assert the MEASURED
virtual-clock latency ≤ that budget, using a chunk-paced synth so the assertion is about
the *cut mechanism* (cooperative abort between small chunks) and not about wall time.

FLAGGED as a Phase-3 real-infra item (see module ``PHASE3_REALINFRA_NOTE``): the true p95
onset→silence latency on the LIVE Recall Output-Media + Cartesia Sonic timing path is a
real-vendor measurement that cannot be produced offline. This oracle proves the cut
*mechanism* meets budget on a deterministic virtual clock; the vendor-timing SLO p95 must
still be measured against live Recall/Cartesia in Phase 3.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

pytestmark = pytest.mark.latency

#: §3.6 barge-in budget: a human speech onset must stop TTS in under 200 ms.
BARGE_IN_BUDGET_MS = 200.0

#: Phase-3 / real-infra residual this offline oracle deliberately does NOT cover.
PHASE3_REALINFRA_NOTE = (
    "Live p95 onset→silence latency on the real Recall Output-Media + Cartesia Sonic "
    "timing path is a real-vendor measurement; measure against live infra in Phase 3."
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _VirtualClock:
    """A monotonic clock we advance by hand — no wall-time flake, exact latency math.

    ``now()`` is the injected ``TurnController(now=...)`` source; ``advance`` steps virtual
    time. The chunk-paced synth advances it per emitted chunk so an onset→stop measurement
    reflects how many chunks actually reached the sink, i.e. how deep the cut bit.
    """

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, ms: float) -> None:
        self._t += ms / 1000.0


class _RecordingSink:
    """A real ``OutputMediaSink`` shape: records every written chunk + flush instants.

    ``write_audio`` yields to the loop (``asyncio.sleep(0)``) so a concurrently-scheduled
    barge-in gets a turn between chunks — exactly the cooperative point the real cut uses.
    """

    def __init__(self, clock: _VirtualClock) -> None:
        self._clock = clock
        self.written: list[int] = []          # chunk seqs that reached the sink
        self.write_times_ms: list[float] = []  # virtual instant each chunk landed
        self.flushed_at_ms: list[float] = []

    async def write_audio(self, chunk) -> None:  # noqa: ANN001 — AudioChunk (test seam)
        self.written.append(chunk.seq)
        self.write_times_ms.append(self._clock.now() * 1000.0)
        await asyncio.sleep(0)  # cooperative yield: let a pending barge-in interleave

    async def flush(self) -> None:
        self.flushed_at_ms.append(self._clock.now() * 1000.0)

    async def write_frame(self, frame) -> None:  # noqa: ANN001 — CanvasFrame (unused here)
        return None


class _ChunkPacedTTS:
    """A ``TTSProvider`` that yields MANY small chunks, each costing virtual time.

    This is the adversary for AC-TURN-10: a *large buffered stretch*. If the cut were
    defeated by buffering, all ``n_chunks`` would reach the sink. A real mid-word cut must
    stop after only a few chunks — well within budget — no matter how long the utterance is.
    Each yielded chunk advances the injected clock by ``chunk_ms`` so the sink's recorded
    write-times give a real onset→stop latency.
    """

    def __init__(self, clock: _VirtualClock, *, n_chunks: int, chunk_ms: float) -> None:
        self._clock = clock
        self._n = n_chunks
        self._chunk_ms = chunk_ms

    def synthesize(self, text: str) -> AsyncIterator:  # noqa: ANN001,ARG002 — AudioChunk stream
        clock, n, chunk_ms = self._clock, self._n, self._chunk_ms

        async def gen():
            from transport.media import AudioChunk

            for i in range(n):
                # Each small chunk costs a slice of virtual time BEFORE it is handed to the
                # controller — a long utterance is a long tail of these small chunks.
                clock.advance(chunk_ms)
                yield AudioChunk(pcm=b"x" * 32, seq=i, is_final=(i == n - 1))
                await asyncio.sleep(0)  # let the controller's abort check / cancel land

        return gen()


def _controller(clock: _VirtualClock, tts):  # noqa: ANN001 — shared builder
    from transport.carrier import SignalCarrier
    from transport.turn import TurnController

    return TurnController(tts=tts, sink=_RecordingSink(clock), carrier=SignalCarrier()), clock


# ── AC-TURN-15 · MEASURED onset→stop within budget ─────────────────────────────


def test_vad_onset_cuts_tts_midword_within_budget():
    """AC-TURN-15/07/10: a VAD onset stops in-flight TTS mid-word ≤ budget; big buffer loses.

    criterion_id: AC-TURN-15
    """
    from transport.carrier import SignalCarrier
    from transport.turn import TurnController, VadFrame

    clock = _VirtualClock()
    # A LONG utterance: 500 small 10ms chunks = a 5s buffered stretch. If buffering could
    # defeat the cut, ~500 chunks would land. A real mid-word cut must bite in a few chunks.
    n_chunks, chunk_ms = 500, 10.0
    tts = _ChunkPacedTTS(clock, n_chunks=n_chunks, chunk_ms=chunk_ms)
    sink = _RecordingSink(clock)
    ctrl = TurnController(tts=tts, sink=sink, carrier=SignalCarrier(), now=clock.now)

    async def run() -> tuple[int, float]:
        # Speak only on a real boundary — start the (long) utterance streaming.
        ctrl.enqueue("a long uninterruptible-looking sentence that Proxy is mid-way through")
        await ctrl.on_boundary()
        assert ctrl.speaking, "utterance must be in-flight before the barge-in"

        # Let a few chunks stream so we are genuinely MID-WORD, then record the onset instant.
        for _ in range(3):
            await asyncio.sleep(0)
        chunks_before = len(sink.written)
        onset_ms = clock.now() * 1000.0

        # A HUMAN speech onset arrives on the VAD path (not the transcript path).
        await ctrl.on_vad_frame(VadFrame(speaker_id="alice", is_speech=True, t=clock.now()))

        # Drain any remaining scheduling so a (hypothetically) un-cut stream could finish.
        for _ in range(n_chunks + 5):
            await asyncio.sleep(0)
        return chunks_before, onset_ms

    chunks_before, onset_ms = _run(run())

    # 1) The cut actually landed mid-word: far fewer than all chunks reached the sink.
    assert 0 < chunks_before < n_chunks, "TTS must have been mid-utterance at onset"
    assert len(sink.written) < n_chunks, (
        f"barge-in failed to cut: {len(sink.written)}/{n_chunks} chunks reached the sink — "
        "a large buffered stretch defeated the cut (AC-TURN-10 violation)"
    )

    # 2) MEASURED latency: onset → last chunk that reached the sink, on the virtual clock.
    last_write_ms = sink.write_times_ms[-1] if sink.write_times_ms else onset_ms
    cut_latency_ms = max(0.0, last_write_ms - onset_ms)
    assert cut_latency_ms <= BARGE_IN_BUDGET_MS, (
        f"onset→stop {cut_latency_ms:.1f}ms exceeds the {BARGE_IN_BUDGET_MS:.0f}ms budget "
        f"(§3.6); {len(sink.written) - chunks_before} chunks slipped through after onset"
    )

    # 3) The queue AND the Output-Media buffer were flushed atomically on the cut.
    assert ctrl.queue_len == 0, "barge-in must flush the pending utterance queue (AC-TURN-08)"
    assert sink.flushed_at_ms, "barge-in must flush the Output-Media sink (AC-TURN-07)"
    # After the cut the FSM returns to IDLE — no longer speaking.
    assert not ctrl.speaking


def test_large_buffer_cannot_defeat_the_cut_chunk_bound():
    """AC-TURN-10: making the utterance 10x longer does NOT widen the cut — few chunks slip.

    criterion_id: AC-TURN-15
    """
    from transport.carrier import SignalCarrier
    from transport.turn import TurnController, VadFrame

    def slipped_chunks(n_chunks: int) -> int:
        clock = _VirtualClock()
        tts = _ChunkPacedTTS(clock, n_chunks=n_chunks, chunk_ms=10.0)
        sink = _RecordingSink(clock)
        ctrl = TurnController(tts=tts, sink=sink, carrier=SignalCarrier(), now=clock.now)

        async def run() -> int:
            ctrl.enqueue("x")
            await ctrl.on_boundary()
            for _ in range(3):
                await asyncio.sleep(0)
            before = len(sink.written)
            await ctrl.on_vad_frame(VadFrame(speaker_id="bob", is_speech=True, t=clock.now()))
            for _ in range(n_chunks + 5):
                await asyncio.sleep(0)
            return len(sink.written) - before

        return _run(run())

    short = slipped_chunks(50)
    long = slipped_chunks(500)  # 10x the buffer
    # The number of chunks that slip through AFTER onset is bounded by the cooperative
    # abort mechanism (≤1 in-flight chunk), NOT by how long the utterance is.
    assert long <= short + 1, (
        f"a 10x-longer buffer let {long} chunks slip vs {short} — the buffer is defeating "
        "the cut (AC-TURN-10 violation)"
    )
    assert long <= 1, f"more than one chunk slipped past the mid-word cut ({long})"


# ── AC-TURN-01 / AC-TURN-11 · barge-in is VAD-sourced, never Proxy/silence ──────


def test_barge_in_does_not_fire_on_proxy_or_silence():
    """AC-TURN-11: a Proxy-labelled frame and a silence frame never trigger barge-in.

    criterion_id: AC-TURN-11
    """
    from transport.carrier import SignalCarrier
    from transport.hearing import PROXY_SPEAKER
    from transport.turn import TurnController, VadFrame

    clock = _VirtualClock()
    tts = _ChunkPacedTTS(clock, n_chunks=200, chunk_ms=10.0)
    sink = _RecordingSink(clock)
    ctrl = TurnController(tts=tts, sink=sink, carrier=SignalCarrier(), now=clock.now)

    async def run() -> None:
        ctrl.enqueue("Proxy is speaking and should not cut itself off")
        await ctrl.on_boundary()
        for _ in range(3):
            await asyncio.sleep(0)
        assert ctrl.speaking

        before = len(sink.written)
        # (a) Proxy's OWN audio, labelled as PROXY_SPEAKER — must NOT be a barge-in onset.
        await ctrl.on_vad_frame(VadFrame(speaker_id=PROXY_SPEAKER, is_speech=True, t=clock.now()))
        for _ in range(5):
            await asyncio.sleep(0)
        assert ctrl.speaking, "Proxy's own audio must not trigger barge-in (AC-TURN-11)"

        # (b) A silence frame — never an onset.
        await ctrl.on_vad_frame(VadFrame(speaker_id="alice", is_speech=False, t=clock.now()))
        for _ in range(5):
            await asyncio.sleep(0)
        assert ctrl.speaking, "a silence frame must not trigger barge-in (AC-TURN-11)"

        # The stream kept flowing across both non-onsets (proof the cut path was not taken).
        assert len(sink.written) > before, "TTS must keep streaming through Proxy/silence frames"

        # Sanity: a genuine HUMAN onset DOES cut — the negatives above weren't a broken path.
        await ctrl.on_vad_frame(VadFrame(speaker_id="alice", is_speech=True, t=clock.now()))
        for _ in range(210):
            await asyncio.sleep(0)
        assert not ctrl.speaking, "a real human onset must cut (proves the negatives were real)"

    _run(run())


def test_barge_in_source_is_vad_not_transcript():
    """AC-TURN-01: the human-onset decision folds a VadFrame, with no transcript dependency.

    criterion_id: AC-TURN-01
    """
    from transport.signals import Speaking
    from transport.turn import SpeakingDetector, VadFrame

    det = SpeakingDetector()
    # First human speech edge → room speaking-on signal AND a human onset, purely from VAD.
    signal, human_onset = det.observe(VadFrame(speaker_id="alice", is_speech=True, t=1.0))
    assert human_onset is True
    assert isinstance(signal, Speaking) and signal.on is True
    # A second frame from the SAME speaker is not a new onset (edge-triggered, not level).
    _, onset_again = det.observe(VadFrame(speaker_id="alice", is_speech=True, t=1.02))
    assert onset_again is False


# ── AC-TURN-02 / 05 / 06 · boundary opens ONLY on a real end_of_turn ────────────


def test_boundary_opens_only_on_real_end_of_turn_never_a_timer():
    """AC-TURN-02/05/06: on_stt_message opens a boundary only on a true end_of_turn.

    criterion_id: AC-TURN-02
    """
    from transport.boundary import BoundarySource
    from transport.carrier import SignalCarrier
    from transport.signals import Boundary
    from transport.turn import TurnController, TurnSignalPump

    clock = _VirtualClock()
    tts = _ChunkPacedTTS(clock, n_chunks=5, chunk_ms=10.0)
    carrier = SignalCarrier()
    ctrl = TurnController(tts=tts, sink=_RecordingSink(clock), carrier=carrier, now=clock.now)
    pump = TurnSignalPump(
        carrier, ctrl, now=clock.now, boundary_source=BoundarySource.AAI_END_OF_TURN
    )

    boundaries: list[Boundary] = []

    async def run() -> None:
        sub = carrier.subscribe()

        async def collect() -> None:
            async for sig in sub:
                if isinstance(sig, Boundary):
                    boundaries.append(sig)

        collector = asyncio.ensure_future(collect())
        ctrl.enqueue("queued until a REAL boundary opens")

        # A mid-thought breath: end_of_turn absent / falsy — NOT a boundary (no timer fires).
        await pump.on_stt_message({"words": "so anyway I was", "speaker": "Alice"})
        await pump.on_stt_message({"words": "thinking that", "speaker": "Alice", "end_of_turn": False})
        for _ in range(5):
            await asyncio.sleep(0)
        assert boundaries == [], "no boundary may open without a real end_of_turn (AC-TURN-06)"
        assert not ctrl.speaking, "voice must NOT start without a boundary (AC-TURN-05)"
        assert ctrl.queue_len == 1, "the utterance stays queued until a real boundary"

        # A REAL end_of_turn → exactly one boundary opens and the queued utterance releases.
        await pump.on_stt_message({"words": "let's ship it.", "speaker": "Alice", "end_of_turn": True})
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(boundaries) == 1, "a real end_of_turn opens exactly one boundary (AC-TURN-02)"
        assert ctrl.queue_len == 0, "the boundary released the queued utterance (AC-TURN-05)"

        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

    _run(run())


def test_boundary_predicate_rejects_timerish_messages():
    """AC-TURN-02: boundary_opened is a pure end_of_turn predicate — never elapsed-time.

    criterion_id: AC-TURN-02
    """
    from transport.turn import boundary_opened

    assert boundary_opened({"end_of_turn": True, "words": "done."}) is True
    assert boundary_opened({"end_of_turn": False, "words": "um"}) is False
    assert boundary_opened({"words": "no field at all"}) is False
    # A message carrying only elapsed-silence / timer-ish fields is NOT a boundary.
    assert boundary_opened({"silence_ms": 5000, "elapsed_ms": 9999}) is False


# ── AC-TURN-12 / 13 / 14 · hard-mute: voice off, tile+chat live, speaking XOR muted ─


def test_hard_mute_silences_voice_but_keeps_tile_and_chat_live():
    """AC-TURN-12/13/14: hard-mute kills TTS, voice off, tile+chat live; speaking XOR muted.

    criterion_id: AC-TURN-12
    """
    from transport.carrier import SignalCarrier
    from transport.turn import TurnController, VadFrame

    clock = _VirtualClock()
    tts = _ChunkPacedTTS(clock, n_chunks=300, chunk_ms=10.0)
    sink = _RecordingSink(clock)
    ctrl = TurnController(tts=tts, sink=sink, carrier=SignalCarrier(), now=clock.now)

    async def run() -> None:
        ctrl.enqueue("banked utterance one")
        ctrl.enqueue("banked utterance two")
        await ctrl.on_boundary()  # starts utterance one; two stays queued
        for _ in range(3):
            await asyncio.sleep(0)
        assert ctrl.speaking

        await ctrl.hard_mute()
        for _ in range(5):
            await asyncio.sleep(0)

        # Voice off, in-flight TTS killed, queue flushed.
        assert ctrl.muted and not ctrl.speaking, "speaking XOR muted (AC-TURN-14)"
        assert ctrl.voice_on is False, "voice must be OFF in silent mode (AC-TURN-12)"
        assert ctrl.queue_len == 0, "hard-mute flushes the queue"
        assert sink.flushed_at_ms, "hard-mute must flush in-flight TTS"

        # Tile + chat stay live through the mute (AC-TURN-13).
        assert ctrl.tile_on is True and ctrl.chat_on is True

        # A VAD onset while muted does NOT re-enable voice; only a re-invite lifts it.
        await ctrl.on_vad_frame(VadFrame(speaker_id="alice", is_speech=True, t=clock.now()))
        for _ in range(5):
            await asyncio.sleep(0)
        assert ctrl.muted, "voice must stay muted until a re-invite (AC-TURN-14)"

        ctrl.re_invite()
        assert not ctrl.muted and ctrl.voice_on is True, "re-invite restores voice (AC-TURN-12)"

    _run(run())


# ── Phase-3 / real-infra residual (explicitly flagged, deliberately not faked) ──


def test_phase3_realinfra_latency_slo_is_flagged_not_faked():
    """The live Recall/Cartesia p95 onset→silence SLO is a Phase-3 real-infra measurement.

    criterion_id: AC-TURN-15
    This offline oracle proves the cut MECHANISM meets budget on a deterministic clock; it
    does NOT (and must not pretend to) measure the real-vendor timing p95. We assert the
    residual is recorded as a flag rather than silently claimed green.
    """
    assert "Phase 3" in PHASE3_REALINFRA_NOTE
    assert "Recall" in PHASE3_REALINFRA_NOTE and "Cartesia" in PHASE3_REALINFRA_NOTE
