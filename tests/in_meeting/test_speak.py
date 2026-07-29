"""Acceptance battery for SPEAK-SINK — the real speak channel pipe
(``in_meeting.speak``): engine TEXT deltas → sentence buffer → injected
``synthesize`` → the per-meeting Output-Media channel.

Deterministic and offline: ``synthesize`` is a scripted fake yielding
AudioChunk-shaped objects (``pcm``/``seq``/``is_final``); the channel is a
recording fake. No real Cartesia, no network. Seven AC[det] criteria:

1. sentence flush — deltas accumulate; each completed sentence (terminator
   followed by whitespace/end) synthesizes immediately, exact text, in order,
   and every yielded pcm chunk reaches the channel in order;
2. tail flush — a trailing partial with no terminator synthesizes only after
   the (injectable) quiet window elapses;
3. speaking state — ``set_speaking(True)`` before the first audio of an
   utterance, ``False`` once idle; never toggled per-chunk (exact sequence);
4. ordering under slow synth — sentence 1 slow, sentence 2 queued → ALL of
   sentence 1's pcm lands before any of sentence 2's (one synth at a time);
5. never-throw — a raising synthesize doesn't crash the pipe; later sentences
   still synthesize; ``last_error`` records the fault honestly;
6. cut() — buffered text + queued sentences dropped immediately; a subsequent
   ``say`` starts clean;
7. alignment — odd-length pcm chunks reach the channel as 2-byte-aligned
   writes only (s16le playback breaks on misalignment); no audio byte lost.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from in_meeting.speak import SpeakPipe, build_speak_sink

# A quiet window long enough that it NEVER fires inside a test that exercises
# sentence-boundary flushing (those flushes must come from the terminator).
_NEVER = 5.0
# The tiny injectable window for the tail-flush test.
_TINY = 0.05


@dataclass(frozen=True)
class _Chunk:
    """AudioChunk-shaped (transport.media.AudioChunk): pcm / seq / is_final."""

    pcm: bytes
    seq: int
    is_final: bool = False


@dataclass
class _RecordingChannel:
    """The Output-Media channel surface the pipe writes into, recorded."""

    calls: list[tuple[str, bytes | bool]] = field(default_factory=list)

    async def write_audio(self, pcm: bytes) -> None:
        self.calls.append(("audio", pcm))

    async def set_speaking(self, speaking: bool) -> None:
        self.calls.append(("speaking", speaking))

    @property
    def audio(self) -> list[bytes]:
        return [payload for kind, payload in self.calls if kind == "audio" and isinstance(payload, bytes)]


def _scripted_synth(script: dict[str, list[bytes]], calls: list[str]):
    """A scripted fake synthesize: text → its pcm chunks, calls recorded."""

    async def synthesize(text: str) -> AsyncIterator[_Chunk]:
        calls.append(text)
        chunks = script[text]
        for seq, pcm in enumerate(chunks):
            yield _Chunk(pcm=pcm, seq=seq, is_final=(seq == len(chunks) - 1))

    return synthesize


# ---------------------------------------------------------------------------
# 1. Sentence-boundary flush: exact sentences, exact order, all pcm in order.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentence_flush_exact_sentences_in_order() -> None:
    calls: list[str] = []
    script = {
        "On it.": [b"\xaa\xaa"],
        "The retry logic is in client.py.": [b"\xbb\xbb", b"\xcc\xcc"],
    }
    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=_scripted_synth(script, calls), channel=channel, flush_after_s=_NEVER)

    await pipe.say("On it. The retry")
    await asyncio.sleep(0.02)
    # The completed sentence synthesized immediately; the partial did NOT.
    assert calls == ["On it."]

    await pipe.say(" logic is in client.py.")
    await pipe.flush()
    assert calls == ["On it.", "The retry logic is in client.py."]
    assert channel.audio == [b"\xaa\xaa", b"\xbb\xbb", b"\xcc\xcc"]


# ---------------------------------------------------------------------------
# 2. Quiet-window tail flush: a trailing partial flushes only after the window.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_flush_after_quiet_window() -> None:
    calls: list[str] = []
    script = {"almost done": [b"\x01\x02"]}
    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=_scripted_synth(script, calls), channel=channel, flush_after_s=_TINY)

    await pipe.say("almost done")
    await asyncio.sleep(0.02)
    assert calls == []  # no terminator, window not yet elapsed → nothing synthesized

    await asyncio.sleep(0.1)  # let the quiet window elapse
    assert calls == ["almost done"]
    assert channel.audio == [b"\x01\x02"]
    assert isinstance(pipe, SpeakPipe)


# ---------------------------------------------------------------------------
# 3. Speaking state: True before first audio, False once idle, never per-chunk.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speaking_state_wraps_utterance_not_chunks() -> None:
    calls: list[str] = []
    script = {"One.": [b"\x11\x11", b"\x22\x22"], "Two.": [b"\x33\x33"]}
    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=_scripted_synth(script, calls), channel=channel, flush_after_s=_NEVER)

    await pipe.say("One. Two.")
    await pipe.flush()

    # Exact sequence: one True before ANY audio, all audio, one False after.
    assert channel.calls == [
        ("speaking", True),
        ("audio", b"\x11\x11"),
        ("audio", b"\x22\x22"),
        ("audio", b"\x33\x33"),
        ("speaking", False),
    ]


# ---------------------------------------------------------------------------
# 4. Ordering: one synth at a time — slow sentence 1 fully lands before 2.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordering_under_slow_synth() -> None:
    calls: list[str] = []
    s1 = [b"\x01\x01", b"\x02\x02", b"\x03\x03"]
    s2 = [b"\x0a\x0a", b"\x0b\x0b"]

    async def synthesize(text: str) -> AsyncIterator[_Chunk]:
        calls.append(text)
        chunks = s1 if text == "First one." else s2
        for seq, pcm in enumerate(chunks):
            if text == "First one.":
                await asyncio.sleep(0.01)  # slow synth for sentence 1
            yield _Chunk(pcm=pcm, seq=seq, is_final=(seq == len(chunks) - 1))

    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=synthesize, channel=channel, flush_after_s=_NEVER)

    await pipe.say("First one. Second one.")  # both queued at once
    await pipe.flush()

    assert calls == ["First one.", "Second one."]
    assert channel.audio == s1 + s2  # ALL of sentence 1 before ANY of sentence 2


# ---------------------------------------------------------------------------
# 5. Never-throw: a synth failure is an honest no-audio, recorded, not raised.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synth_failure_never_throws_and_pipe_survives() -> None:
    calls: list[str] = []
    boom = RuntimeError("cartesia down")

    async def synthesize(text: str) -> AsyncIterator[_Chunk]:
        calls.append(text)
        if text == "Boom.":
            raise boom
        yield _Chunk(pcm=b"\x0f\x0f", seq=0, is_final=True)

    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=synthesize, channel=channel, flush_after_s=_NEVER)

    await pipe.say("Boom. Fine.")  # neither say() nor the pipe may raise
    await pipe.flush()

    assert calls == ["Boom.", "Fine."]  # the later sentence still synthesized
    assert channel.audio == [b"\x0f\x0f"]  # honest no-audio for the failed one
    assert pipe.last_error is boom


# ---------------------------------------------------------------------------
# 6. cut(): buffered text + queued sentences dropped; next say starts clean.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cut_drops_buffer_and_queue() -> None:
    calls: list[str] = []
    release = asyncio.Event()

    async def synthesize(text: str) -> AsyncIterator[_Chunk]:
        calls.append(text)
        if text == "Sentence one.":
            await release.wait()  # holds the worker in-flight until cut
        yield _Chunk(pcm=b"\x0c\x0d", seq=0, is_final=True)

    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=synthesize, channel=channel, flush_after_s=_NEVER)

    await pipe.say("Sentence one. Sentence two. trailing tail")
    await asyncio.sleep(0.02)  # worker is in-flight on sentence one
    await pipe.cut()

    await pipe.say("Fresh.")
    await pipe.flush()

    # Sentence two + the buffered tail were dropped; the next say ran clean.
    assert calls == ["Sentence one.", "Fresh."]
    assert channel.audio == [b"\x0c\x0d"]  # only Fresh.'s audio ever landed


# ---------------------------------------------------------------------------
# 7. Alignment: odd-length chunks → only 2-byte-aligned writes, no byte lost.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_odd_length_chunks_written_two_byte_aligned() -> None:
    calls: list[str] = []
    script = {"Odd tail.": [b"\x01\x02\x03", b"\x04\x05"]}  # 3 + 2 = 5 bytes
    channel = _RecordingChannel()
    pipe = build_speak_sink(synthesize=_scripted_synth(script, calls), channel=channel, flush_after_s=_NEVER)

    await pipe.say("Odd tail.")
    await pipe.flush()

    assert channel.audio, "audio must have been written"
    assert all(len(pcm) % 2 == 0 for pcm in channel.audio)
    # Odd carry rides into the next chunk; the final dangling byte is padded
    # with one zero byte to complete the s16 sample — nothing dropped.
    assert b"".join(channel.audio) == b"\x01\x02\x03\x04\x05\x00"
