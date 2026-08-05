"""Provider-independence seams (§3.8): ``TransportProvider`` (Recall),
``TTSProvider`` (Cartesia), and the ``OutputMediaSink``.

Every external piece sits behind a thin ``Protocol`` so a provider swap is a
migration, not a redesign (AC-SEAM-01/02/03/04). Callers depend ONLY on these
Protocols — never on a concrete provider client type; the concrete SDK/client
symbol appears solely inside its impl module, and every round-trip is wrapped by
``libs.http.call_external`` (AC-XCUT-03). V0 runs the managed stack end to end.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from .media import AudioChunk, CanvasFrame


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
    """The meeting carrier (Recall.ai): join, chat/dm, Output Media.

    One API spans Meet/Zoom/Teams — zero per-platform code lives above this seam.
    """

    async def join(self, meeting_link: str) -> str:
        """Join from a link alone (no host install); return the bot id."""
        ...

    async def leave(self, bot_id: str) -> None: ...

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...

    def output_media(self, bot_id: str) -> OutputMediaSink: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech (Cartesia Sonic 3): text in → small audio chunks out (§3.3)."""

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]: ...
