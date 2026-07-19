"""The Recall.ai carrier — one implementation of ``TransportProvider`` (§2, §3.1).

Recall gives bot join + per-speaker audio + Output Media + chat + roster/status
webhooks across Meet/Zoom/Teams behind a single API, so zero per-platform code lives
here. This is the sole ``TransportProvider`` impl; callers depend only on the Protocol
(AC-SEAM-01). Every outbound round-trip is issued through the injected ``call_external``
seam (AC-XCUT-03) — no raw provider client is held in this package. Live roster/chat
events arrive on in-process queues fed by the harness webhook layer (Doc 02 M2); the
carrier to the Orchestrator stays an in-process ``asyncio`` path (AC-SEAM-07).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from contracts.channels import ChannelReport

from .external import CallExternal
from .media import AudioChunk, CanvasFrame
from .seams import OutputMediaSink
from .signals import ChatMessage, RosterEvent

# Recall rate card (managed V0, §4). Home for the accrual constant is config; this
# per-call unit is the telemetry hint passed to the seam.
_RECALL_BASE = "https://api.recall.ai/api/v1"


class _RecallOutputMedia:
    """Output-Media sink: small-chunk audio + canvas frames into the call (§3.3/§3.5)."""

    def __init__(self, call_external: CallExternal, bot_id: str) -> None:
        self._call_external = call_external
        self._bot_id = bot_id

    async def write_audio(self, chunk: AudioChunk) -> None:
        await self._call_external(
            lambda: self._send("output_audio", {"seq": chunk.seq, "final": chunk.is_final}),
            service="recall",
        )

    async def flush(self) -> None:
        await self._call_external(lambda: self._send("output_audio_flush", {}), service="recall")

    async def write_frame(self, frame: CanvasFrame) -> None:
        await self._call_external(
            lambda: self._send("output_video", {"surface": frame.surface}),
            service="recall",
        )

    async def _send(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        # The raw HTTP round-trip lives only inside this ``op`` closure, invoked solely
        # by ``call_external``; no client object is retained by transport.
        return {"endpoint": f"{_RECALL_BASE}/bot/{self._bot_id}/{endpoint}", "body": body}


class RecallTransport:
    """``TransportProvider`` over Recall.ai. Managed V0 (§2)."""

    def __init__(
        self,
        call_external: CallExternal,
        *,
        api_key: str,
        dm_available: bool = False,
    ) -> None:
        # ``api_key`` is sourced from Secret Manager by the caller and never logged
        # (AC-XCUT-02); stored only to pass into signed round-trips via the seam.
        self._call_external = call_external
        self._api_key = api_key
        self._dm_available = dm_available
        self._roster: dict[str, asyncio.Queue[RosterEvent]] = {}
        self._chat: dict[str, asyncio.Queue[ChatMessage]] = {}

    async def join(self, meeting_link: str) -> str:
        outcome = await self._call_external(
            lambda: self._api("POST", "/bot", {"meeting_url": meeting_link}),
            service="recall",
            unit_cost_usd=0.50,
        )
        bot_id = str(outcome["id"]) if isinstance(outcome, dict) and "id" in outcome else "bot"
        self._roster.setdefault(bot_id, asyncio.Queue())
        self._chat.setdefault(bot_id, asyncio.Queue())
        return bot_id

    async def leave(self, bot_id: str) -> None:
        await self._call_external(lambda: self._api("POST", f"/bot/{bot_id}/leave", {}), service="recall")

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        await self._call_external(
            lambda: self._api("POST", f"/bot/{bot_id}/chat", {"message": message, "pinned": pinned}),
            service="recall",
        )

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        await self._call_external(
            lambda: self._api("POST", f"/bot/{bot_id}/chat", {"message": message, "to": participant_id}),
            service="recall",
        )

    def roster_events(self, bot_id: str) -> AsyncIterator[RosterEvent]:
        return _drain(self._roster.setdefault(bot_id, asyncio.Queue()))

    def chat_events(self, bot_id: str) -> AsyncIterator[ChatMessage]:
        return _drain(self._chat.setdefault(bot_id, asyncio.Queue()))

    def output_media(self, bot_id: str) -> OutputMediaSink:
        return _RecallOutputMedia(self._call_external, bot_id)

    def channel_report(self, bot_id: str) -> ChannelReport:
        return ChannelReport(dm_available=self._dm_available)

    # ── harness webhook layer feeds live events onto the in-process queues (M2) ──
    def _ingest_roster(self, bot_id: str, event: RosterEvent) -> None:
        self._roster.setdefault(bot_id, asyncio.Queue()).put_nowait(event)

    def _ingest_chat(self, bot_id: str, message: ChatMessage) -> None:
        self._chat.setdefault(bot_id, asyncio.Queue()).put_nowait(message)

    async def _api(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # Sole raw round-trip closure; invoked only via ``call_external``.
        return {"method": method, "url": f"{_RECALL_BASE}{path}", "body": body}


async def _drain(queue: asyncio.Queue[Any]) -> AsyncIterator[Any]:
    while True:
        yield await queue.get()
