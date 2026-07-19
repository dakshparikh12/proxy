"""Provider-independence seams (§3.8): ``TransportProvider`` (Recall), ``STTProvider``
(AssemblyAI-via-Recall), ``TTSProvider`` (Cartesia), and the ``OutputMediaSink``.

Every external piece sits behind a thin ``Protocol`` so a provider swap is a
migration, not a redesign (AC-SEAM-01/02/03/04). Callers depend ONLY on these
Protocols — never on a concrete provider client type; the concrete SDK/client
symbol appears solely inside its impl module, and every round-trip is wrapped by
``libs.http.call_external`` (AC-XCUT-03). V0 runs the managed stack end to end.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from contracts.channels import ChannelReport

from .media import AudioChunk, CanvasFrame
from .signals import ChatMessage, RosterEvent, Transcript


@runtime_checkable
class OutputMediaSink(Protocol):
    """The Recall Output-Media audio/video sink — where synthesized speech and the
    canvas are streamed into the call. Small-chunk audio keeps barge-in honest (§3.3)."""

    async def write_audio(self, chunk: AudioChunk) -> None: ...

    async def flush(self) -> None:
        """Drop buffered/in-flight audio (barge-in): at most one small chunk survives."""
        ...

    async def write_frame(self, frame: CanvasFrame) -> None: ...


@runtime_checkable
class TransportProvider(Protocol):
    """The meeting carrier (Recall.ai): join, roster/chat/status events, Output Media.

    One API spans Meet/Zoom/Teams — zero per-platform code lives above this seam.
    """

    async def join(self, meeting_link: str) -> str:
        """Join from a link alone (no host install); return the bot id."""
        ...

    async def leave(self, bot_id: str) -> None: ...

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...

    def roster_events(self, bot_id: str) -> AsyncIterator[RosterEvent]: ...

    def chat_events(self, bot_id: str) -> AsyncIterator[ChatMessage]: ...

    def output_media(self, bot_id: str) -> OutputMediaSink: ...

    def channel_report(self, bot_id: str) -> ChannelReport:
        """Which channels this meeting supports (dm_available) — reported, not judged (§3.4)."""
        ...


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-text (AssemblyAI Universal-Streaming via Recall BYOK passthrough).

    No Proxy-side STT client is instantiated (AC-HEAR-02): transcripts arrive on the
    Recall passthrough and are parsed here. Also carries the ``boundary`` field
    (AAI ``end_of_turn``) consumed by turn-taking (§3.6).
    """

    def transcripts(self, bot_id: str) -> AsyncIterator[Transcript]: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech (Cartesia Sonic 3): text in → small audio chunks out (§3.3)."""

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]: ...
