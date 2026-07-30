"""AssemblyAI-via-Recall BYOK passthrough — one implementation of ``STTProvider`` (§3.2).

STT is AssemblyAI Universal-Streaming with **our key configured in Recall** (BYOK): the
audio path is Recall→AssemblyAI direct, so **no Proxy-side STT client is instantiated**
(AC-HEAR-02) — we only *consume* Recall's transcript passthrough. Each passthrough
message is parsed by the fail-loud wire parser (AC-HEAR-12); drift raises rather than
being read on a silent assumption. Proxy's own speech is marked and filtered upstream
(§3.2); this layer just yields the attributed transcript records.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .signals import Transcript
from .wire import parse_transcript


class RecallPassthroughSTT:
    """``STTProvider`` fed solely by Recall's AssemblyAI passthrough (no STT SDK here)."""

    def __init__(self) -> None:
        self._streams: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def transcripts(self, bot_id: str) -> AsyncIterator[Transcript]:
        queue = self._streams.setdefault(bot_id, asyncio.Queue())
        return self._parse_stream(queue)

    async def _parse_stream(self, queue: asyncio.Queue[dict[str, Any]]) -> AsyncIterator[Transcript]:
        while True:
            message = await queue.get()
            # Fail-loud on wire drift (AC-HEAR-12): a bad shape raises, never a wrong record.
            yield parse_transcript(message)

    def _ingest(self, bot_id: str, message: dict[str, Any]) -> None:
        """Recall passthrough feeds a raw transcript message onto the in-process stream."""
        self._streams.setdefault(bot_id, asyncio.Queue()).put_nowait(message)
