"""Cartesia Sonic 3 — one implementation of ``TTSProvider`` (§3.3).

Text in → Cartesia synthesizes (~40ms time-to-first-audio) → small audio chunks out,
streamed to Recall Output Media so a barge-in flush drops at most one in-flight chunk
(§3.3). The synthesis round-trip is issued through the injected ``call_external`` seam
(AC-XCUT-03); no raw Cartesia client is held in this package. One calm voice/register —
the actual audible synth + latency proof lands in M5; the swappable seam lives here.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from .external import CallExternal
from .media import AudioChunk

_CARTESIA_BASE = "https://api.cartesia.ai"


# One calm voice, one register across every line (brand: the teal "deep feel", §3.3).
# A single configured (voice_id, register) is used for ALL synthesis so the run carries
# exactly one voice identity (AC-SPEAK-02) — never varied line-to-line.
_DEFAULT_VOICE_ID = "proxy-calm"
_DEFAULT_REGISTER = "calm"


class CartesiaTTS:
    """``TTSProvider`` over Cartesia Sonic 3 (managed V0, §2) — one voice, one register."""

    def __init__(
        self,
        call_external: CallExternal,
        *,
        api_key: str,
        chunk_ms: int = 250,
        voice_id: str = _DEFAULT_VOICE_ID,
        register: str = _DEFAULT_REGISTER,
    ) -> None:
        # ``api_key`` from Secret Manager, never logged (AC-XCUT-02).
        self._call_external = call_external
        self._api_key = api_key
        self._chunk_ms = chunk_ms
        self._voice_id = voice_id
        self._register = register

    @property
    def voice(self) -> tuple[str, str]:
        """The single (voice_id, register) every synthesis carries (AC-SPEAK-02)."""
        return (self._voice_id, self._register)

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(text)

    async def _stream(self, text: str) -> AsyncIterator[AudioChunk]:
        outcome = await self._call_external(
            lambda: self._synth(text),
            service="cartesia",
            unit_cost_usd=0.0,
        )
        chunks = outcome.get("chunks", 0) if isinstance(outcome, dict) else 0
        for seq in range(int(chunks)):
            yield AudioChunk(pcm=b"", seq=seq, is_final=(seq == int(chunks) - 1))

    async def _synth(self, text: str) -> dict[str, object]:
        # Sole raw round-trip closure; invoked only via ``call_external``. The single
        # configured voice/register rides every request (AC-SPEAK-02).
        return {
            "url": f"{_CARTESIA_BASE}/tts/bytes",
            "text_len": len(text),
            "voice_id": self._voice_id,
            "register": self._register,
            "chunks": 0,
        }
