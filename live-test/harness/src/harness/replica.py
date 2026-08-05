"""Replica speaking bots — the SAME primitives ``services/in-meeting`` uses.

Each human speaker (Pranav, Riya, ...) is a "replica": a real Recall bot that
joins the meeting with a per-replica Output-Media webpage as its camera+mic, and
speaks a line by

1. synthesizing the exact text with **Cartesia** (``transport.tts.CartesiaTTS``),
2. writing the PCM chunks into that replica's Output-Media **channel**
   (``in_meeting.output_media.channel_for`` — the page the Recall bot loads in its
   headless browser plays them as the bot's microphone).

This is byte-for-byte the same audio path Proxy uses to speak, so a replica line
reaches Proxy through real STT exactly as a human would. Every Recall/Cartesia
round-trip rides ``libs.http.call_external`` via the reused transport — no raw
vendor client lives here. The whole class is constructed from injected
collaborators so an offline test drives it with in-process fakes (no network).
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


class AudioChunkLike(Protocol):
    """One synthesized audio chunk — structurally ``transport.media.AudioChunk``."""

    @property
    def pcm(self) -> bytes: ...


# The product modules (in_meeting / transport / libs.http) are imported by the LIVE
# factory only (``build_live_replicas``), never at module import, so an offline test
# needs none of them on the path — it injects fakes conforming to the Protocols below.


class OutputChannel(Protocol):
    """The per-replica Output-Media channel the page plays as the bot's mic.

    Structurally ``in_meeting.output_media.OutputMediaChannel``.
    """

    async def write_audio(self, pcm: bytes) -> None: ...

    async def set_speaking(self, speaking: bool) -> None: ...


class TransportLike(Protocol):
    """The meeting carrier — structurally ``transport.recall.RecallTransport``."""

    async def join(self, meeting_link: str) -> str: ...

    async def leave(self, bot_id: str) -> None: ...


class TTSLike(Protocol):
    """Text→audio — structurally ``transport.tts.CartesiaTTS`` (``.synthesize``)."""

    def synthesize(self, text: str) -> AsyncIterator[AudioChunkLike]: ...


def _slug(name: str) -> str:
    """A URL/channel-safe id from a speaker name (``Riya`` → ``riya``)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "replica"


@dataclass
class Replica:
    """One speaking bot: its Recall bot id + its Output-Media channel + its TTS."""

    speaker: str
    channel_id: str  # the output-media channel/page id (per replica)
    transport: TransportLike
    tts: TTSLike
    channel: OutputChannel
    bot_id: str | None = None
    #: The lines this replica has spoken (SAID) — the driver mirrors these into the
    #: run log; kept here too for a per-replica sanity check.
    said: list[str] = field(default_factory=list)

    async def join(self, meeting_url: str) -> str:
        """Join the meeting (idempotent — re-join returns the existing bot id)."""
        if self.bot_id is None:
            self.bot_id = await self.transport.join(meeting_url)
        return self.bot_id

    async def speak(self, text: str) -> int:
        """Synthesize ``text`` and stream it into this replica's channel.

        Returns the number of PCM chunks written (so a caller/test can assert the
        audio actually rode the right channel). The speaking pulse brackets the
        line exactly as the product speak path does.
        """
        await self.channel.set_speaking(True)
        chunks = 0
        async for chunk in self.tts.synthesize(text):
            await self.channel.write_audio(chunk.pcm)
            chunks += 1
        await self.channel.set_speaking(False)
        self.said.append(text)
        return chunks

    async def leave(self) -> None:
        if self.bot_id is not None:
            await self.transport.leave(self.bot_id)
            self.bot_id = None


def build_live_replicas(
    speakers: list[str],
    *,
    recall_api_key: str,
    cartesia_api_key: str,
    output_media_origin: str,
    webhook_url: str = "",
) -> list[Replica]:
    """Construct real Recall+Cartesia replicas — one per speaker (LIVE path).

    Each replica gets its OWN Output-Media page (``<origin>/output-media/<slug>``)
    so its audio never collides with another bot's or with Proxy's. The transport
    + TTS are the product's own, bound to the ``libs.http`` seam. Called only on
    the live path; offline tests build ``Replica`` directly with fakes.
    """
    # Imported HERE (not at module load) so the offline test path stays free of
    # the product transport + libs.http; this is the sole live construction site.
    # Put BOTH the repo root (for ``libs.http``) and the in-meeting ``src`` (for
    # ``transport`` / ``in_meeting``) on the path so the product primitives resolve.
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parents[4]
    _in_meeting_src = _repo_root / "services" / "in-meeting" / "src"
    for _p in (str(_repo_root), str(_in_meeting_src)):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    from in_meeting import output_media
    from transport.recall import RecallTransport
    from transport.tts import CartesiaTTS

    from libs.http.src.http.external import call_external

    origin = output_media_origin.rstrip("/")
    marker = "/output-media"
    idx = origin.find(marker)
    if idx != -1:
        origin = origin[:idx]

    replicas: list[Replica] = []
    seen: dict[str, int] = {}
    for speaker in speakers:
        base = _slug(speaker)
        # Disambiguate duplicate speaker names into distinct channels.
        seen[base] = seen.get(base, 0) + 1
        channel_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        page_url = f"{origin}/output-media/{channel_id}"
        transport = RecallTransport(
            call_external,
            api_key=recall_api_key,
            webhook_url=webhook_url,
            output_media_url=page_url,
        )
        tts = CartesiaTTS(call_external, api_key=cartesia_api_key)
        channel = output_media.channel_for(channel_id)
        replicas.append(
            Replica(
                speaker=speaker,
                channel_id=channel_id,
                transport=transport,
                tts=tts,
                channel=channel,
            )
        )
    return replicas
