"""``fifo_player`` — the REFERENCE implementation of the continuous-stream audio player (FIX 4).

The Output-Media page plays Proxy's voice. The old page scheduled each ~120ms PCM chunk as its
OWN ``AudioBufferSourceNode`` created at 44100 on a context running at the browser's native rate,
so WebAudio resampled EVERY CHUNK SEPARATELY — an interpolation seam at every chunk boundary
(~8 seams/sec = the classic choppy/gravelly voice). The rebuild plays ONE continuous stream: an
``AudioWorklet`` pulls from a single Float32 FIFO that every incoming chunk is APPENDED to — no
per-chunk buffers, no per-chunk resample, no seams.

The worklet's FIFO/prebuffer/underrun/cut logic can't be unit-tested in a browser from here, so
this module is its byte-for-byte MIRROR in pure Python: :class:`FifoPlayer` implements the exact
same append / block-emit / prebuffer / underrun-ramp / cut algorithm the inline ``WORKLET_SRC`` in
``output_media.py`` runs. ``tests/test_output_media_stream_player.py`` drives THIS with realistic
jittered chunk arrivals and asserts the properties the founder demanded — zero seams where no
underrun occurs, prebuffer respected, ramped (not truncated) underrun silence, instant cut. Keep
the two in lockstep: a change to the worklet algorithm must land here (and vice-versa).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FifoPlayer:
    """A pull-based continuous player over a single sample FIFO (the worklet's mirror).

    ``block`` is the render-quantum size (128 in WebAudio). ``prebuffer``/``ramp`` are in SAMPLES
    (the page derives them from seconds × sampleRate). Feed with :meth:`append`; render one output
    block at a time with :meth:`process` (returns exactly ``block`` samples). :meth:`cut` clears the
    FIFO instantly (barge-in). The emitted sample stream is BYTE-IDENTICAL to the concatenated input
    wherever no underrun occurred — the seam-free guarantee — with ramped fades only at gap edges.
    """

    prebuffer: int
    ramp: int
    block: int = 128

    _fifo: list[float] = field(default_factory=list)
    _read: int = 0
    _priming: bool = True
    _fading_in: int = 0

    def available(self) -> int:
        return len(self._fifo) - self._read

    def append(self, samples: list[float]) -> None:
        """Append decoded float samples to the FIFO (the ``{type:'samples'}`` port message)."""
        if self._read > 0 and self._read >= len(self._fifo):
            self._fifo = []
            self._read = 0
        if self._read > 0:
            self._fifo = self._fifo[self._read :]
            self._read = 0
        self._fifo.extend(samples)

    def cut(self) -> None:
        """Barge-in: drop everything buffered and re-prime (the ``{type:'cut'}`` port message)."""
        self._fifo = []
        self._read = 0
        self._priming = True
        self._fading_in = 0

    def process(self) -> list[float]:
        """Render ONE output block. Mirrors the worklet's ``process`` exactly.

        * priming: after silence, emit all-zero blocks until at least ``prebuffer`` samples are
          banked (the jitter cushion), then arm a fade-in so the resume is click-free;
        * steady: pop one FIFO sample per output slot; while fading in, scale up over ``ramp``;
        * underrun mid-block: fade the already-written tail out over ``ramp``, zero the remainder,
          re-prime — no truncation, no click.
        """
        out = [0.0] * self.block
        if self._priming:
            if self.available() < self.prebuffer:
                return out  # all zeros — still banking the prebuffer
            self._priming = False
            self._fading_in = self.ramp

        for i in range(self.block):
            if self.available() <= 0:
                # UNDERRUN: ramp the tail we already wrote this block down to zero, zero the rest.
                start = max(0, i - self.ramp)
                span = (i - start) or 1
                for j in range(start, i):
                    out[j] *= (i - j) / span
                for k in range(i + 1, self.block):
                    out[k] = 0.0
                self._priming = True
                self._fading_in = 0
                return out
            s = self._fifo[self._read]
            self._read += 1
            if self._fading_in > 0:
                s *= 1 - self._fading_in / self.ramp
                self._fading_in -= 1
            out[i] = s
        return out


__all__ = ["FifoPlayer"]
