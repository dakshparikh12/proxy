"""services.transport — the in-process voice/transport package inside ``meeting_runtime``
(spec Doc 02). Provider-independent seams (Recall / AssemblyAI-via-Recall / Cartesia),
the §3.10 signal surface, and the in-process asyncio carrier. No voice-agent framework,
no bus/broker/wire, no ``libs/transport``: Recall owns transport, this is the thin glue.
"""
from __future__ import annotations

from .boundary import BoundaryDecision, BoundarySource, resolve_boundary_source
from .carrier import SignalCarrier
from .external import CallExternal
from .fakes import FakeOutputMediaSink, FakeTTS
from .media import AudioChunk, CanvasFrame
from .recall import RecallTransport
from .seams import OutputMediaSink, STTProvider, TransportProvider, TTSProvider
from .signals import (
    EMITTED_SIGNAL_NAMES,
    BargeIn,
    BotStatus,
    Boundary,
    ChannelReportSignal,
    ChatMessage,
    MeetingEnd,
    RosterEvent,
    Signal,
    Speaking,
    Transcript,
    signal_name,
)
from .stt import RecallPassthroughSTT
from .tts import CartesiaTTS
from .wire import WireDriftError, parse_transcript

__all__ = [
    "AudioChunk",
    "BargeIn",
    "BotStatus",
    "Boundary",
    "BoundaryDecision",
    "BoundarySource",
    "CallExternal",
    "CanvasFrame",
    "CartesiaTTS",
    "ChannelReportSignal",
    "ChatMessage",
    "EMITTED_SIGNAL_NAMES",
    "FakeOutputMediaSink",
    "FakeTTS",
    "MeetingEnd",
    "OutputMediaSink",
    "RecallPassthroughSTT",
    "RecallTransport",
    "RosterEvent",
    "STTProvider",
    "Signal",
    "SignalCarrier",
    "Speaking",
    "TTSProvider",
    "Transcript",
    "TransportProvider",
    "WireDriftError",
    "parse_transcript",
    "resolve_boundary_source",
    "signal_name",
]
