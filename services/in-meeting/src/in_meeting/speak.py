"""SPEAK-SINK — the real speak channel: engine text → synth → Output-Media.

The Engine streams spoken TEXT deltas into an injected async ``speak`` sink
(``engine.SpeakFn``). N1 built the real Cartesia synth
(``transport.tts.CartesiaTTS.synthesize(text) → AsyncIterator[AudioChunk]``,
s16le 44.1 kHz mono pcm); OUTPUT-MEDIA built the per-meeting channel that plays
pcm in the meeting (``output_media.channel_for(meeting_id)``). This module is
the PIPE between them — pure physics, no situation→action:

* **Sentence-boundary buffering** — deltas accumulate; a sentence is complete
  at a terminator (``.`` ``!`` ``?``) followed by whitespace or the current
  end of buffer (a simple, documented heuristic — abbreviations/decimals may
  split early; acceptable for speech pacing). Completed sentences synthesize
  immediately: low first-word latency without per-token synth round-trips.
* **Quiet-window tail flush** — a trailing partial (no terminator) flushes
  after ``flush_after_s`` (default 0.5 s) of no new deltas; the timer resets
  per delta and is injectable so tests stay deterministic.
* **Ordering** — ONE synth in flight at a time (a FIFO worker task drains the
  sentence queue); overlapping synths would interleave audio.
* **Speaking state** — ``set_speaking(True)`` right before the FIRST audio
  write of an utterance, ``set_speaking(False)`` only once the pipe is idle
  (queue drained AND no tail buffered) — never toggled per chunk, and held
  True across a pending tail so the orb doesn't flicker mid-utterance.
* **2-byte alignment** — Cartesia's tail chunk can be odd-length; an odd byte
  is CARRIED into the next chunk, and a final dangling byte is padded with one
  zero byte (half an s16 sample of silence — inaudible; s16le playback breaks
  on misaligned writes, so every ``write_audio`` payload is even-length).
* **Never-throw** — a fault while piping a sentence is swallowed into an
  honest no-audio for that sentence, whether the SYNTH raised or the CHANNEL
  did (``write_audio``/``set_speaking``) — the engine already "spoke" the
  text; the turn must not crash. The fault is recorded on ``last_error``.
  Cancellation is never swallowed.
* **``cut()``** — the barge-in PRIMITIVE only: drops the buffered text, the
  queued sentences, and the in-flight synth immediately (detection lives in
  the barge-in reflex, not here). **``aclose()``** — meeting end: flush the
  tail, wait out the queue, leave speaking False.

Injection: ``synthesize`` and ``channel`` are structural (Protocol) — tests
pass fakes; the cutover/boot node wires the real ``CartesiaTTS`` + channel.
``real_speak_sink`` is the one production convenience: it imports
``transport.tts`` LAZILY inside the function because in-meeting doesn't
declare transport as a dependency (the same tracked pattern as
``meeting_control``'s structural transport Protocol).

``SpeakPipe`` satisfies BOTH engine speak shapes: ``engine.SpeakFn`` (it is
async-callable) and ``engine.SpeakSink`` (it carries async ``say``).
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Protocol

__all__ = ["AudioChunkLike", "AudioOut", "SpeakPipe", "SynthesizeFn", "build_speak_sink", "real_speak_sink"]

#: Sentence terminators — a terminator followed by whitespace/end closes a sentence.
_TERMINATORS = frozenset(".!?")

#: Default quiet window (seconds) before a trailing partial is flushed to synth.
_DEFAULT_FLUSH_AFTER_S = 0.5


class AudioChunkLike(Protocol):
    """The shape of one synth chunk — structurally ``transport.media.AudioChunk``.

    ``pcm`` is a read-only property so frozen dataclasses (the real
    ``transport.media.AudioChunk``) conform structurally.
    """

    @property
    def pcm(self) -> bytes: ...


#: The injected synth: text in → an async stream of pcm chunks out
#: (``CartesiaTTS.synthesize`` conforms; tests pass a scripted fake).
SynthesizeFn = Callable[[str], AsyncIterator[AudioChunkLike]]


class AudioOut(Protocol):
    """The Output-Media channel surface this pipe writes into (structural —
    ``output_media.OutputMediaChannel`` conforms; tests pass a recorder)."""

    async def write_audio(self, pcm: bytes) -> None: ...

    async def set_speaking(self, speaking: bool) -> None: ...


def _split_sentences(text: str) -> tuple[list[str], str]:
    """Split ``text`` into (completed sentences, trailing partial).

    A sentence completes at a terminator (``.!?``) followed by whitespace or
    the end of ``text``; the whitespace between sentences is consumed. Simple
    and documented: ``client.py.`` at end-of-buffer closes (the inner dots are
    followed by letters, so they don't), while abbreviations/decimals may
    close a beat early — fine for speech pacing.
    """
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _TERMINATORS and (i + 1 == n or text[i + 1].isspace()):
            sentence = text[start : i + 1].strip()
            if sentence:
                sentences.append(sentence)
            i += 1
            while i < n and text[i].isspace():
                i += 1
            start = i
        else:
            i += 1
    return sentences, text[start:]


class SpeakPipe:
    """The per-meeting speak pipe: text deltas → sentences → synth → channel.

    ``say`` (and ``__call__`` — the ``engine.SpeakFn`` shape) accepts arbitrary
    text fragments and never raises on a synth or channel fault mid-sentence;
    ``flush`` forces the tail out and drains; ``cut`` is the barge-in
    primitive; ``aclose`` is meeting end. ``last_error`` carries the most
    recent piping fault, honestly.
    """

    def __init__(
        self,
        *,
        synthesize: SynthesizeFn,
        channel: AudioOut,
        flush_after_s: float = _DEFAULT_FLUSH_AFTER_S,
    ) -> None:
        self._synthesize = synthesize
        self._channel = channel
        self._flush_after_s = flush_after_s
        self._buffer = ""
        self._queue: deque[str] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._tail_timer: asyncio.Task[None] | None = None
        self._speaking = False
        #: Monotonic timestamp until which the ROOM is still audibly playing what we wrote.
        #: Synthesis outruns playback (Cartesia returns a 10s answer's PCM in ~1-2s), so the
        #: write-state alone goes idle while the page still has seconds of scheduled audio —
        #: the live barge-in gap: ``speaking`` must reflect AUDIBILITY, not write activity.
        self._audible_until = 0.0
        #: The most recent synth fault (never raised into the engine's turn).
        self.last_error: Exception | None = None

    # -- the engine-facing surface --------------------------------------------

    @property
    def speaking(self) -> bool:
        """True while an utterance is IN FLIGHT — the barge-in trigger's guard.

        Mid-utterance means any of: audio is actively playing (``set_speaking(True)``
        landed and the pipe hasn't gone idle), the synth worker is mid-sentence, a
        sentence is queued, or un-flushed text is buffered awaiting its tail timer —
        i.e. exactly the state :meth:`cut` would silence. An idle pipe is False, so a
        barge-in trigger reading this never cuts on nothing.
        """
        worker = self._worker
        worker_live = worker is not None and not worker.done()
        audible = time.monotonic() < self._audible_until
        return (
            self._speaking or worker_live or bool(self._queue) or bool(self._buffer) or audible
        )

    async def say(self, text: str) -> None:
        """Accept one TEXT delta; synthesize any newly completed sentences."""
        if not text:
            return
        completed, self._buffer = _split_sentences(self._buffer + text)
        if completed:
            self._queue.extend(completed)
            self._ensure_worker()
        self._arm_tail_timer()

    async def __call__(self, text: str) -> None:
        """``engine.SpeakFn`` shape — the pipe IS the speak sink."""
        await self.say(text)

    async def flush(self) -> None:
        """Force the trailing partial out now and drain the whole queue."""
        self._cancel_tail_timer()
        tail = self._buffer.strip()
        self._buffer = ""
        if tail:
            self._queue.append(tail)
        # Run the worker even with nothing queued: a whitespace-only buffer
        # strips to no tail, but it may have blocked the worker's idle branch
        # (the only place set_speaking(False) lands) — the wake-up lets the
        # idle path fire; a no-op worker exits immediately.
        self._ensure_worker()
        while True:
            worker = self._worker
            if worker is None or worker.done():
                return
            # Absorb the worker's terminal state (incl. a cut() cancelling it
            # mid-drain) without re-raising into the caller.
            await asyncio.gather(worker, return_exceptions=True)

    async def commit_tail(self) -> None:
        """Turn-boundary commit: push this turn's buffered trailing partial into
        the FIFO queue NOW, so a following turn's first delta can't concatenate
        onto it and synthesize as one merged sentence.

        Unlike :meth:`flush`, this does NOT wait for the queue to drain — the
        worker keeps synthesizing in the background and cross-turn FIFO order is
        preserved, so it never over-serializes concurrent turns (the caller holds
        the one-mouth lock only long enough to close its own utterance)."""
        self._cancel_tail_timer()
        tail = self._buffer.strip()
        self._buffer = ""
        if tail:
            self._queue.append(tail)
            self._ensure_worker()

    async def cut(self) -> None:
        """Barge-in primitive: drop buffered text, queued sentences, and the
        in-flight synth NOW. Detection lives in the barge-in reflex, not here.

        Dropping the host-side state is not enough: the PAGE has already SCHEDULED
        seconds of WebAudio from PCM we streamed, so the room keeps hearing the
        interrupted turn unless the cut PROPAGATES into the page. We therefore fire
        the channel's ``cut`` too (drop wire-buffered PCM + send the page a cut
        control frame so it stops every scheduled source). Duck-typed so a plain
        recorder fake without ``cut`` still works (only ``set_speaking``/``write_audio``
        are required by ``AudioOut``)."""
        self._cancel_tail_timer()
        self._buffer = ""
        self._queue.clear()
        self._audible_until = 0.0  # the page dumps its scheduled audio on the cut frame
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        channel_cut = getattr(self._channel, "cut", None)
        if callable(channel_cut):
            await channel_cut()  # drop wire-buffered PCM + tell the page to stop playback NOW
        if self._speaking:
            self._speaking = False
            await self._channel.set_speaking(False)

    async def aclose(self) -> None:
        """Meeting end: flush the tail, wait for in-flight synth, go quiet."""
        await self.flush()
        if self._speaking:  # belt-and-braces; the worker drops it on idle
            self._speaking = False
            await self._channel.set_speaking(False)

    # -- internal machinery ----------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """The FIFO drain: one sentence's synth at a time, in queue order."""
        while True:
            if self._queue:
                # Drain EVERYTHING queued as one synth unit: the first sentence rides
                # alone (it queued first — fast start), and sentences that accumulated
                # while it synthesized ride together, so the voice keeps ONE prosody
                # arc instead of restarting cadence per sentence (live founder finding:
                # per-sentence synthesis sounded disjointed).
                parts = [self._queue.popleft()]
                while self._queue:
                    parts.append(self._queue.popleft())
                sentence = " ".join(parts)
                try:
                    await self._pipe_sentence(sentence)
                except Exception as exc:  # never-throw: honest no-audio (Law 2)
                    self.last_error = exc
                continue
            if self._speaking and not self._buffer:
                # Idle: queue drained and no tail pending → utterance over.
                self._speaking = False
                await self._channel.set_speaking(False)
                continue  # a delta may have landed during the await — re-check
            return  # no awaits between this check and exit: no lost wake-ups

    async def _pipe_sentence(self, sentence: str) -> None:
        """Synthesize ONE sentence and stream its pcm, 2-byte-aligned."""
        carry = b""
        async for chunk in self._synthesize(sentence):
            pcm = carry + chunk.pcm
            carry = b""
            if len(pcm) % 2:
                carry = pcm[-1:]  # odd byte rides into the next chunk
                pcm = pcm[:-1]
            if pcm:
                await self._write(pcm)
        if carry:
            # A dangling odd byte at stream end: pad to a full s16 sample.
            await self._write(carry + b"\x00")

    async def _write(self, pcm: bytes) -> None:
        if not self._speaking:
            self._speaking = True
            await self._channel.set_speaking(True)
        await self._channel.write_audio(pcm)
        # Track how long the ROOM stays audible: each s16 mono chunk buys
        # len/(2*rate) seconds of playback from max(now, the current horizon).
        from transport.tts import SAMPLE_RATE_HZ
        now = time.monotonic()
        base = self._audible_until if self._audible_until > now else now
        self._audible_until = base + len(pcm) / (2.0 * SAMPLE_RATE_HZ)

    def _arm_tail_timer(self) -> None:
        """(Re)start the quiet-window timer — reset on every delta."""
        self._cancel_tail_timer()
        if self._buffer:
            self._tail_timer = asyncio.create_task(self._tail_flush())

    def _cancel_tail_timer(self) -> None:
        timer = self._tail_timer
        self._tail_timer = None
        if timer is not None and not timer.done():
            timer.cancel()

    async def _tail_flush(self) -> None:
        await asyncio.sleep(self._flush_after_s)
        self._tail_timer = None
        tail = self._buffer.strip()
        self._buffer = ""
        if tail:
            self._queue.append(tail)
        # Ensure the worker runs even when the tail stripped to NOTHING: a
        # whitespace-only buffer (e.g. a trailing "\n" delta) blocked the
        # worker's idle branch, and set_speaking(False) only lands there —
        # without this wake-up the orb stays lit forever.
        self._ensure_worker()


def build_speak_sink(
    *,
    synthesize: SynthesizeFn,
    channel: AudioOut,
    flush_after_s: float = _DEFAULT_FLUSH_AFTER_S,
) -> SpeakPipe:
    """The injected-parts constructor (tests + the cutover node's wiring).

    The returned ``SpeakPipe`` is itself an ``engine.SpeakFn`` (async-callable)
    and an ``engine.SpeakSink`` (async ``say``) — hand it to the Engine as-is.
    """
    return SpeakPipe(synthesize=synthesize, channel=channel, flush_after_s=flush_after_s)


def real_speak_sink(
    meeting_id: str,
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
    chunk_ms: int | None = None,
    flush_after_s: float = _DEFAULT_FLUSH_AFTER_S,
) -> SpeakPipe:
    """Production convenience: the REAL Cartesia synth → this meeting's channel.

    ``transport.tts`` is imported LAZILY here because in-meeting doesn't declare
    transport as a dependency (the same tracked pattern as ``meeting_control``'s
    structural transport Protocol); the synth's round-trips ride the one
    ``libs.http.call_external`` seam (retry + cost telemetry). ``api_key`` etc.
    default to ``CartesiaTTS``'s own settings surfaces when not injected.
    """
    from transport.tts import CartesiaTTS

    from libs.http.src.http.external import call_external

    from .output_media import channel_for

    tts = CartesiaTTS(call_external, api_key=api_key, chunk_ms=chunk_ms, voice_id=voice_id)
    return build_speak_sink(
        synthesize=tts.synthesize, channel=channel_for(meeting_id), flush_after_s=flush_after_s
    )
