"""transport — the thin vendor edges to the live meeting (Recall / AssemblyAI-via-Recall /
Cartesia) + the §3.10 signal surface. Recall owns transport; this is the glue. The old
voice-agent M-suite (boundary/projector/resolution/turn/etc.) and the in-process carrier +
webhook-event fan (carrier/events/hearing/chat/failure) were deleted in the workroom pivot —
Proxy's turn-taking + presentation are the agent's judgment now, not a pipeline.
"""
from __future__ import annotations

from .consent import consent_notice, notice_is_valid
from .external import CallExternal
from .join import Action, ConsentGate, JoinResult, JoinSession, JoinSource, JoinState
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
    MeetingMetadata,
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
    "Action",
    "AudioChunk",
    "BargeIn",
    "MeetingMetadata",
    "BotStatus",
    "Boundary",
    "CallExternal",
    "CanvasFrame",
    "CartesiaTTS",
    "ChannelReportSignal",
    "ConsentGate",
    "ChatMessage",
    "EMITTED_SIGNAL_NAMES",
    "JoinResult",
    "JoinSession",
    "JoinSource",
    "JoinState",
    "MeetingEnd",
    "OutputMediaSink",
    "RecallPassthroughSTT",
    "RecallTransport",
    "RosterEvent",
    "STTProvider",
    "Signal",
    "Speaking",
    "TTSProvider",
    "Transcript",
    "TransportProvider",
    "WireDriftError",
    "consent_notice",
    "notice_is_valid",
    "parse_transcript",
    "signal_name",
]
