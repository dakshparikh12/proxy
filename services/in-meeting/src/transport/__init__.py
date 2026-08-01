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
from .seams import OutputMediaSink, TransportProvider, TTSProvider
from .signals import (
    ChatMessage,
    RosterEvent,
    Speaking,
    Transcript,
)
from .tts import CartesiaTTS

__all__ = [
    "Action",
    "AudioChunk",
    "CallExternal",
    "CanvasFrame",
    "CartesiaTTS",
    "ConsentGate",
    "ChatMessage",
    "JoinResult",
    "JoinSession",
    "JoinSource",
    "JoinState",
    "OutputMediaSink",
    "RecallTransport",
    "RosterEvent",
    "Speaking",
    "TTSProvider",
    "Transcript",
    "TransportProvider",
    "consent_notice",
    "notice_is_valid",
]
