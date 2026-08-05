"""transport — the thin vendor edges to the live meeting (Recall / AssemblyAI-via-Recall /
Cartesia). Recall owns transport; this is the glue. The old voice-agent M-suite
(boundary/projector/resolution/turn/etc.) and the in-process carrier + webhook-event fan
(carrier/events/hearing/chat/failure) plus the transport-emitted signal surface were deleted
in the workroom pivot — Proxy's turn-taking + presentation are the agent's judgment now, and
the transcript reaches it via the meeting webhook drain, not a pipeline.
"""
from __future__ import annotations

from .consent import consent_notice, notice_is_valid
from .external import CallExternal
from .join import Action, JoinResult, JoinSession, JoinSource, JoinState
from .media import AudioChunk, CanvasFrame
from .recall import RecallTransport
from .seams import OutputMediaSink, TransportProvider, TTSProvider
from .tts import CartesiaTTS

__all__ = [
    "Action",
    "AudioChunk",
    "CallExternal",
    "CanvasFrame",
    "CartesiaTTS",
    "JoinResult",
    "JoinSession",
    "JoinSource",
    "JoinState",
    "OutputMediaSink",
    "RecallTransport",
    "TTSProvider",
    "TransportProvider",
    "consent_notice",
    "notice_is_valid",
]
