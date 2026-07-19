"""Hearing: audio in → speaker-attributed transcript out (§3.2 / §3.9-3).

This is the HEAR stage. Recall delivers **per-speaker audio streams**; STT is
AssemblyAI Universal-Streaming via Recall's BYOK passthrough (no Proxy-side STT
client — that lives behind :class:`~transport.stt.RecallPassthroughSTT`, AC-HEAR-02).
This module owns the thin glue above the passthrough:

- **Per-speaker ingest** (:class:`AudioIngest`): one keyed stream per Recall speaker
  id, never merged into a single undifferentiated channel (AC-HEAR-01). It is the
  Silero-VAD substrate for turn-taking (M4); STT itself flows on the passthrough.
- **Transcript fan-out** (:class:`HearingStage`): the single transcript stream fans to
  BOTH Doc 04 (Orchestrator) and Doc 03 (Notes) over one carrier, identical ordered
  records to each subscriber (AC-HEAR-04); every record keeps its speaker label and
  timestamps intact (AC-HEAR-03/06/07).
- **Self-loop guard** (AC-HEAR-08/09): Proxy's own transcribed speech is present in the
  record labelled ``Proxy`` but is **never** forwarded on the ask-routing path — a
  ``Proxy`` line is untrusted, inert data (CANONICAL §10.3), never an instruction.
  Suppression is **speaker-scoped** (keyed on ``speaker == 'Proxy'``), so every
  human line is forwarded and no human ask is dropped.

**BYOK honesty (AC-HEAR-11).** The Recall→AssemblyAI leg is external, so Proxy does
**not** own buffer-through-a-hiccup resilience and this module contains **no
resume-after-gap / buffer-during-outage handler**. The honest fallback is the
**mark-lost** path: any un-transcribed stretch is surfaced as an explicit gap marker
(:meth:`HearingStage.mark_lost`), never silently absent. (Confirm-at-build: the wire
shape is pinned in :mod:`transport.wire`; the leg's buffering is not ours to promise.)
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .carrier import SignalCarrier
from .media import AudioFrame
from .seams import STTProvider
from .signals import Transcript

#: The reserved speaker label for Proxy's own transcribed speech (§3.2). Kept as a
#: single constant so the self-loop guard keys on speaker identity, not content.
PROXY_SPEAKER = "Proxy"


class AudioIngest:
    """Per-speaker inbound audio: one keyed stream per Recall speaker, never merged.

    ``ingest`` routes a frame to *only* its speaker's stream (AC-HEAR-01); overlapping
    speakers therefore land on distinct streams with independent frame counts. This is
    the VAD substrate — the transcript itself arrives on the Recall passthrough.
    """

    def __init__(self) -> None:
        self._frames: dict[str, list[AudioFrame]] = {}

    def ingest(self, frame: AudioFrame) -> None:
        """Append a frame to its speaker's stream only — no cross-speaker merge."""
        self._frames.setdefault(frame.speaker_id, []).append(frame)

    def stream(self, speaker_id: str) -> tuple[AudioFrame, ...]:
        """The frames ingested for one speaker, in arrival order."""
        return tuple(self._frames.get(speaker_id, ()))

    def speaker_ids(self) -> frozenset[str]:
        """Every distinct speaker seen — its cardinality is the live stream count."""
        return frozenset(self._frames)

    def stream_count(self) -> int:
        """Number of distinct per-speaker streams (== number of speakers)."""
        return len(self._frames)


@dataclass(frozen=True)
class EmittedRecord:
    """One transcript record with the monotonic instant it was emitted onto the stream.

    ``emit_t`` lets the word-latency measurement (AC-HEAR-05) compute
    ``emit_t - spoken_t`` per word without the stage owning the STT-internal buffering
    of the external Recall→AssemblyAI leg.
    """

    record: Transcript
    emit_t: float
    routed_as_ask: bool


@dataclass(frozen=True)
class TranscriptGap:
    """A stretch of audio that was NOT transcribed — marked lost, never silently absent.

    Emitted by :meth:`HearingStage.mark_lost` when the external STT leg drops audio
    (§3.7): the record honestly reflects the gap window; Proxy never claims it buffered
    through it (AC-HEAR-11). Distinct from a spoken :class:`~transport.signals.Transcript`
    record so the transcript-shape oracle (AC-HEAR-03) never sees a speakerless record.
    """

    t_start: float
    t_end: float
    reason: str = "stt_gap"


class HearingStage:
    """Fan the one transcript stream to Doc 03 + Doc 04 and gate ask-routing by speaker.

    The stage is a thin loop: read each parsed :class:`Transcript` off the passthrough,
    emit it once onto the shared carrier (both consumers get the identical ordered
    sequence — AC-HEAR-04), then — and only for non-``Proxy`` speakers — forward it on
    the injected ask-routing path (AC-HEAR-08/09).
    """

    def __init__(
        self,
        stt: STTProvider,
        carrier: SignalCarrier,
        *,
        ask_sink: Callable[[Transcript], Awaitable[None]],
        on_gap: Callable[[TranscriptGap], Awaitable[None]] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stt = stt
        self._carrier = carrier
        self._ask_sink = ask_sink
        self._on_gap = on_gap
        self._now = now
        self.emitted: list[EmittedRecord] = []

    async def run(self, bot_id: str) -> None:
        """Consume the passthrough transcript stream until it ends."""
        async for record in self._stt.transcripts(bot_id):
            await self._handle(record)

    async def _handle(self, record: Transcript) -> None:
        # Fan the record to every in-process subscriber over the ONE carrier
        # (Doc 03 Notes + Doc 04 Orchestrator), preserving order + speaker label.
        await self._carrier.emit(record)
        emit_t = self._now()

        # Self-loop guard (AC-HEAR-08/09): a Proxy-labelled line is part of the record
        # but is inert data — never routed as an ask. Suppression keys on speaker only.
        routed_as_ask = record.speaker != PROXY_SPEAKER
        if routed_as_ask:
            await self._ask_sink(record)

        self.emitted.append(EmittedRecord(record=record, emit_t=emit_t, routed_as_ask=routed_as_ask))

    async def mark_lost(self, t_start: float, t_end: float, reason: str = "stt_gap") -> TranscriptGap:
        """Surface an un-transcribed stretch honestly (AC-HEAR-11) — the ONLY gap path.

        There is deliberately no buffer-through / resume-after-gap handler: the external
        BYOK leg's buffering is not ours to promise (§3.2). We mark the window lost.
        """
        gap = TranscriptGap(t_start=t_start, t_end=t_end, reason=reason)
        if self._on_gap is not None:
            await self._on_gap(gap)
        return gap

    def word_latencies(self, spoken_at: Callable[[Transcript], float]) -> list[float]:
        """Per-record ``emit_t - spoken_t`` for the word-latency protocol (AC-HEAR-05)."""
        return [e.emit_t - spoken_at(e.record) for e in self.emitted]

    def asks_forwarded(self) -> list[Transcript]:
        """Records that reached the ask-router (every human line; zero Proxy lines)."""
        return [e.record for e in self.emitted if e.routed_as_ask]


@dataclass
class TranscriptFanout:
    """A test/wire helper: attach N subscribers to one carrier and collect per-consumer
    sequences, so AC-HEAR-04's set- and order-equality can be asserted across consumers.
    """

    carrier: SignalCarrier
    received: dict[str, list[Transcript]] = field(default_factory=dict)

    async def collect(self, name: str, limit: int) -> None:
        seq: list[Transcript] = []
        self.received[name] = seq
        async for signal in self.carrier.subscribe():
            if isinstance(signal, Transcript):
                seq.append(signal)
                if len(seq) >= limit:
                    return
