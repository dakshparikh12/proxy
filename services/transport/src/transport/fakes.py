"""In-process fakes for the confirm-at-build spike and the M4 turn-core (§1 plan).

M4 stands up the TTS→Output-Media small-chunk streamer + speech queue + abort wiring
**driven by these fakes** (a fake ``TTSProvider`` and a fake ``OutputMediaSink``), so the
turn-core greens in isolation before the real Cartesia synth (M5) exists. The fakes are
hermetic and deterministic: the sink records every chunk it received and every flush, so
a barge-in test can prove at most one in-flight chunk survived a mid-word cut.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from .media import AudioChunk, CanvasFrame


class FakeTTS:
    """Deterministic ``TTSProvider``: emits ``chunks`` small audio chunks for any text."""

    def __init__(self, chunks: int = 4) -> None:
        self._chunks = chunks

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[AudioChunk]:
        for seq in range(self._chunks):
            yield AudioChunk(pcm=b"\x00", seq=seq, is_final=(seq == self._chunks - 1))


class FakeOutputMediaSink:
    """Deterministic ``OutputMediaSink`` recording writes + flushes for turn-core proofs."""

    def __init__(self) -> None:
        self.written: list[AudioChunk] = []
        self.frames: list[CanvasFrame] = []
        self.flushes: int = 0

    async def write_audio(self, chunk: AudioChunk) -> None:
        self.written.append(chunk)

    async def flush(self) -> None:
        self.flushes += 1

    async def write_frame(self, frame: CanvasFrame) -> None:
        self.frames.append(frame)
