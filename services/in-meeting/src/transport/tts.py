"""Cartesia Sonic 3 — one implementation of ``TTSProvider`` (§3.3).

Text in → Cartesia's REAL ``/tts/bytes`` streaming synth (~40ms time-to-first-audio)
→ small PCM chunks out, streamed to the Output-Media sink so a barge-in flush drops at
most one in-flight chunk (§3.3). The synthesis round-trip is issued through the injected
``call_external`` seam (AC-XCUT-03); the raw client is constructed ONLY inside
``libs.http`` (``http_client`` — the single raw-client home per §14), imported lazily at
call time, so no raw Cartesia client and no provider SDK live in this package. One calm
voice/register across every line.

PCM format (confirmed against the live docs): raw ``pcm_s16le`` @ 44100 Hz mono —
Cartesia's real ``output_format`` (https://docs.cartesia.ai/api-reference/tts/bytes)
matching Recall's documented in-meeting audio convention (16-bit signed little-endian
PCM, 44.1 kHz, mono), so the synthesized chunks are in the exact format the meeting-side
audio surface consumes.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from . import config
from .external import CallExternal
from .media import AudioChunk

_CARTESIA_BASE = "https://api.cartesia.ai"
# The pinned API version Cartesia's docs require on every request (wire physics).
_CARTESIA_VERSION = "2026-03-01"
# Sonic 3 — the managed-V0 model generation (§2); a real ``model_id`` per the docs.
_MODEL_ID = "sonic-3"
# Raw 16-bit signed little-endian PCM at 44.1 kHz mono. 16 kHz was telephone-band and made
# Proxy audibly worse than every human in the room (live founder finding); 44.1 kHz is full-band,
# Cartesia-supported, and the output-media page resamples cleanly. ONE constant, exported — the
# page and the speak pipe derive from it so the rates can never drift apart.
SAMPLE_RATE_HZ = 44100
_SAMPLE_RATE_HZ = SAMPLE_RATE_HZ
_BYTES_PER_SAMPLE = 2  # s16le, mono
_OUTPUT_FORMAT: dict[str, str | int] = {
    "container": "raw",
    "encoding": "pcm_s16le",
    "sample_rate": _SAMPLE_RATE_HZ,
}
_LANGUAGE = "en"
# Per-round-trip client timeout (seconds) — matches the workspace seam convention;
# the retry policy above this lives in ``call_external``.
_HTTP_TIMEOUT_S = 30.0


# One calm voice, one register across every line (brand: the teal "deep feel", §3.3).
# A single configured (voice_id, register) is used for ALL synthesis so the run carries
# exactly one voice identity (AC-SPEAK-02) — never varied line-to-line. The voice id is
# pinned via the ``CARTESIA_VOICE_ID`` settings surface; the fallback is the library
# voice from Cartesia's own docs example (a real, resolvable id — never invented).
# ``register`` is Proxy's internal register tag: Cartesia's schema carries no register
# field, so it never rides the wire.
_DEFAULT_VOICE_ID = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
_DEFAULT_REGISTER = "calm"


class CartesiaTTS:
    """``TTSProvider`` over Cartesia Sonic 3 (managed V0, §2) — one voice, one register."""

    def __init__(
        self,
        call_external: CallExternal | None = None,
        *,
        api_key: str | None = None,
        chunk_ms: int | None = None,
        voice_id: str | None = None,
        register: str = _DEFAULT_REGISTER,
    ) -> None:
        # ``api_key`` from Secret Manager via the CARTESIA_API_KEY settings surface
        # when not injected; never logged, never placed in a request body (AC-XCUT-02).
        self._call_external = call_external
        self._api_key = api_key if api_key is not None else os.environ.get("CARTESIA_API_KEY", "")
        # Small-chunk size from config — kept below the barge-in stop budget so a surviving
        # in-flight chunk can't defeat barge-in (AC-TURN-10 / AC-SPEAK-08). Single source of
        # truth in config/defaults.toml (Law 4).
        self._chunk_ms = chunk_ms if chunk_ms is not None else config.get_int("tts_chunk_ms")
        self._voice_id = (
            voice_id if voice_id is not None else os.environ.get("CARTESIA_VOICE_ID", _DEFAULT_VOICE_ID)
        )
        self._register = register

    @property
    def voice(self) -> tuple[str, str]:
        """The single (voice_id, register) every synthesis carries (AC-SPEAK-02)."""
        return (self._voice_id, self._register)

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(text)

    async def _stream(self, text: str) -> AsyncIterator[AudioChunk]:
        call_external = self._call_external
        if call_external is None:
            return
        outcome = await call_external(
            lambda: self._synth(text),
            service="cartesia",
            unit_cost_usd=0.0,
        )
        # The seam returns an ``ExternalCallOutcome`` (payload under ``.value``); a
        # fake may hand back the raw payload directly. Duck-type both — honoring the
        # seam contract (AC-XCUT-03) — without coupling transport to ``libs.http``.
        result = getattr(outcome, "value", outcome)
        frames: list[bytes] = result if isinstance(result, list) else []
        last = len(frames) - 1
        for seq, pcm in enumerate(frames):
            yield AudioChunk(pcm=pcm, seq=seq, is_final=(seq == last))

    async def _synth(self, text: str) -> list[bytes]:
        """The sole raw Cartesia round-trip; invoked only via ``call_external`` (AC-XCUT-03).

        POSTs Cartesia's REAL ``/tts/bytes`` streaming request (shape confirmed against
        the live docs): ``model_id`` + ``transcript`` + ``voice`` + ``language`` +
        ``output_format``. The **exact, verbatim** text rides ``transcript`` — no
        headline extraction or substitution (AC-SPEAK-01); the single configured voice
        rides ``voice`` as ``{mode: "id", id}`` on every request (AC-SPEAK-02). Auth is
        ``Authorization: Bearer <key>`` plus the pinned ``Cartesia-Version`` header; the
        key never enters the body and is never logged (AC-XCUT-02).

        The streamed response bytes are collected and re-framed into ≤ ``tts_chunk_ms``
        PCM chunks (44.1 kHz s16le mono ⇒ ~88 bytes/ms) so a surviving in-flight chunk
        can't defeat the mid-word cut (AC-SPEAK-08 / AC-TURN-10). A non-2xx raises
        (honest degrade, Law 2 — retried/absorbed by the seam and the never-throw
        delivery boundary above); the return is EXACTLY the audio Cartesia streamed,
        never fabricated frames.
        """
        from libs.http.src.http.external import http_client

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Cartesia-Version": _CARTESIA_VERSION,
        }
        body: dict[str, object] = {
            "model_id": _MODEL_ID,
            "transcript": text,
            "voice": {"mode": "id", "id": self._voice_id},
            "language": _LANGUAGE,
            "output_format": dict(_OUTPUT_FORMAT),
        }
        pcm = bytearray()
        async with http_client(timeout=_HTTP_TIMEOUT_S) as client:
            async with client.stream(
                "POST", f"{_CARTESIA_BASE}/tts/bytes", headers=headers, json=body
            ) as resp:
                resp.raise_for_status()
                async for part in resp.aiter_bytes():
                    pcm.extend(part)
        step = max(_SAMPLE_RATE_HZ * _BYTES_PER_SAMPLE * self._chunk_ms // 1000, _BYTES_PER_SAMPLE)
        return [bytes(pcm[i : i + step]) for i in range(0, len(pcm), step)]
