"""Media primitives for the Output-Media path (§3.3, §3.5).

Audio is streamed to Output Media in **small chunks** (deliberately ≤ ``tts_chunk_ms``)
so a barge-in flush drops at most one in-flight chunk and the mid-word cut-off stays
honest (§3.3). Canvas frames are the webpage we render and stream as the camera tile
or the promoted screenshare (§3.5) — output-only; we never ingest others' pixels.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFrame:
    """One inbound per-speaker audio frame from Recall (§3.2).

    ``speaker_id`` keys the frame to its Recall speaker so the ingest keeps one
    stream per speaker and never merges the room into a single undifferentiated
    channel (AC-HEAR-01). This is the *incoming* audio unit (Silero VAD substrate);
    :class:`AudioChunk` is the *outgoing* Output-Media unit.
    """

    pcm: bytes
    speaker_id: str
    seq: int


@dataclass(frozen=True)
class AudioChunk:
    """One small Output-Media audio chunk — the unit that keeps barge-in honest."""

    pcm: bytes
    seq: int
    is_final: bool = False


@dataclass(frozen=True)
class CanvasFrame:
    """One rendered canvas frame streamed as the camera tile or the screenshare.

    ``surface`` names which output the same canvas is promoted onto; the two are
    mutually exclusive on the transport (§3.5) — never an inbound presented screen.
    """

    payload: bytes
    width: int
    height: int
    surface: str = "tile"
