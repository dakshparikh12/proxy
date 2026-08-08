"""Cartesia Sonic 3 — one implementation of ``TTSProvider`` (§3.3).

Text in → Cartesia's REAL streaming synth → small PCM chunks out, streamed to the
Output-Media sink so a barge-in flush drops at most one in-flight chunk (§3.3). Both
round-trips are issued through a seam in ``libs.http`` (the sole raw-client home per §14);
transport holds no raw Cartesia/ws client and imports no provider SDK. One calm voice /
register across every line.

TWO synth paths, one contract (``synthesize(text) -> AsyncIterator[AudioChunk]``):

* **WS continuations (default, the TTFA lever)** — a single Cartesia ``/tts/websocket``
  *input-streaming* context: the sentence is pushed as CLAUSE-terminated fragments with
  ``continue: true`` and a lowered ``max_buffer_delay_ms``, so Cartesia begins generating
  the FIRST clause's audio without waiting for the whole sentence, and the PCM frames are
  yielded AS THEY ARRIVE (not buffered whole). First audio starts on the first clause. The
  raw ws client lives ONLY in ``libs.http.ws`` (``call_external_ws`` — retry + cost
  telemetry, same envelope as ``call_external``); this file imports it lazily by seam name.
* **REST ``/tts/bytes`` (honest fallback)** — the original streaming request, collected and
  re-framed. Used automatically when the ws path is unavailable (no API key) OR fails to
  produce ANY audio (connect/auth/handshake fault); the degrade is honest, never silent —
  the ws fault is recorded on ``last_ws_error``. A ws fault AFTER audio has already played
  is re-raised (never REST-replayed) so the room never hears a sentence twice.

PCM format (confirmed against the live docs): raw ``pcm_s16le`` @ 44100 Hz mono —
Cartesia's real ``output_format`` matching Recall's documented in-meeting audio convention
(16-bit signed little-endian PCM, 44.1 kHz, mono), so the synthesized chunks are in the
exact format the meeting-side audio surface consumes. Requesting RAW PCM (not mp3/wav)
skips a decode. SpeakPipe (the caller) still buffers to SENTENCE boundaries before calling
``synthesize``; pushing LLM-generated clauses to the socket the instant they stream would
need a clause-boundary variant in ``in_meeting/speak.py`` (outside this file) — see the
build report.

Cartesia WS protocol (verified against docs.cartesia.ai / the official cartesia-python SDK):
  * Endpoint ``wss://api.cartesia.ai/tts/websocket``; auth + version ride the query string
    (``api_key`` is the documented WS auth; ``cartesia_version`` is the documented WS
    alternative to the ``Cartesia-Version`` header). The seam never logs the URL.
  * Each generation request is a JSON text frame: ``model_id`` · ``transcript`` · ``voice``
    ``{mode:"id", id}`` · ``language`` · ``output_format`` ``{container:"raw",
    encoding:"pcm_s16le", sample_rate}`` · ``context_id`` · ``continue`` (bool). Intermediate
    fragments carry ``continue:true`` + ``max_buffer_delay_ms``; the terminator carries an
    empty ``transcript`` + ``continue:false`` under the SAME ``context_id``.
  * Responses are JSON text frames keyed by ``context_id``: ``type:"chunk"`` with base64
    ``data`` (the raw PCM), ``type:"error"`` with ``error``, and ``type:"done"`` / ``done:
    true`` closing the context.
"""
from __future__ import annotations

import base64
import json
import os
from collections.abc import AsyncIterator
from urllib.parse import urlencode
from uuid import uuid4

from . import config
from .external import CallExternal
from .media import AudioChunk

_CARTESIA_BASE = "https://api.cartesia.ai"
_CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
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
# the retry policy above this lives in ``call_external`` / ``call_external_ws``.
_HTTP_TIMEOUT_S = 30.0
# Lowered input-buffer window for the WS continuations path: how long Cartesia may buffer
# streamed fragments before it starts generating. Small ⇒ the first clause's audio starts
# fast (the whole point); Cartesia still fills to natural boundaries. Env-overridable for a
# per-deploy tune without a code change; kept a plain constant (no new config key needed).
_MAX_BUFFER_DELAY_MS = int(os.environ.get("CARTESIA_WS_BUFFER_MS", "50"))
# Clause boundaries for the WS input-streaming fragments. Splitting on these (delimiter and
# any trailing whitespace kept with the fragment, so concatenation reproduces the text
# EXACTLY) lets Cartesia begin the first clause before the sentence finishes streaming in.
_CLAUSE_BREAKS = frozenset(",;:.!?—")


# One calm voice, one register across every line (brand: the teal "deep feel", §3.3).
# A single configured (voice_id, register) is used for ALL synthesis so the run carries
# exactly one voice identity (AC-SPEAK-02) — never varied line-to-line. The voice id is
# pinned via the ``CARTESIA_VOICE_ID`` settings surface; the fallback is the library
# voice from Cartesia's own docs example (a real, resolvable id — never invented).
# ``register`` is Proxy's internal register tag: Cartesia's schema carries no register
# field, so it never rides the wire.
_DEFAULT_VOICE_ID = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
_DEFAULT_REGISTER = "calm"


def _split_clauses(text: str) -> list[str]:
    """Split ``text`` into clause fragments for the WS input stream.

    A fragment ends after a clause break (``,;:.!?`` or an em-dash) plus any trailing
    whitespace, so the fragments concatenate back to ``text`` byte-for-byte — the split
    changes only WHEN each fragment is pushed to the socket, never the words Cartesia
    speaks (AC-SPEAK-01). Numbers/abbreviations may split across fragments, but Cartesia
    re-buffers the context (``max_buffer_delay_ms``) before generating, so the transcript
    it renders is the exact original.
    """
    fragments: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _CLAUSE_BREAKS:
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            fragments.append(text[start:j])
            start = j
            i = j
        else:
            i += 1
    if start < n:
        fragments.append(text[start:])
    return [f for f in fragments if f]


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
        use_ws: bool = True,
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
        # The WS continuations path needs an API key (it rides the ws query string). With no
        # key, or when explicitly disabled, synthesis takes the REST path unchanged — so the
        # offline test estate (no CARTESIA_API_KEY) exercises exactly the prior behaviour.
        self._use_ws = use_ws and bool(self._api_key) and os.environ.get("CARTESIA_WS_DISABLE") != "1"
        #: The most recent WS-path fault, recorded before an honest REST fallback (Law 2);
        #: never raised into the caller when a fallback is possible, so degrades stay visible.
        self.last_ws_error: Exception | None = None

    @property
    def voice(self) -> tuple[str, str]:
        """The single (voice_id, register) every synthesis carries (AC-SPEAK-02)."""
        return (self._voice_id, self._register)

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(text)

    async def _stream(self, text: str) -> AsyncIterator[AudioChunk]:
        # Prefer the WS continuations path (first audio on the first CLAUSE); fall back to
        # the REST /tts/bytes path on any ws fault that occurs BEFORE audio played (connect /
        # auth / handshake). A fault AFTER frames have played is re-raised, never REST-
        # replayed — the room must never hear the sentence twice (honest degrade, Law 2).
        if self._use_ws:
            yielded = 0
            try:
                async for chunk in self._stream_ws(text):
                    yielded += 1
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 - honest degrade boundary (record, then fall back)
                self.last_ws_error = exc
                if yielded:
                    raise  # partial audio already played — do NOT restart from REST
        async for chunk in self._stream_rest(text):
            yield chunk

    # -- WS continuations path -------------------------------------------------------

    def _ws_url(self) -> str:
        """The ws endpoint with auth + version on the query string (Cartesia's documented
        WS auth). The key rides the query per the vendor protocol; the ``libs.http`` seam
        records only the ``service`` tag, never the URL, so the key is never logged."""
        query = urlencode({"api_key": self._api_key, "cartesia_version": _CARTESIA_VERSION})
        return f"{_CARTESIA_WS_URL}?{query}"

    def _ws_request(self, context_id: str, transcript: str, *, cont: bool) -> dict[str, object]:
        """One Cartesia generation frame (shape verified against the SDK): full body every
        message; intermediate fragments carry ``continue:true`` + the lowered buffer window,
        the terminator carries an empty ``transcript`` + ``continue:false``."""
        request: dict[str, object] = {
            "model_id": _MODEL_ID,
            "transcript": transcript,
            "voice": {"mode": "id", "id": self._voice_id},
            "language": _LANGUAGE,
            "output_format": dict(_OUTPUT_FORMAT),
            "context_id": context_id,
            "continue": cont,
        }
        if cont:
            request["max_buffer_delay_ms"] = _MAX_BUFFER_DELAY_MS
        return request

    async def _stream_ws(self, text: str) -> AsyncIterator[AudioChunk]:
        """One ``/tts/websocket`` input-streaming context: push the sentence's clauses as
        ``continue:true`` fragments, then a ``continue:false`` terminator, and yield PCM
        frames (≤ ``tts_chunk_ms`` each, for barge-in honesty) AS THEY ARRIVE."""
        from libs.http.src.http.ws import call_external_ws

        clauses = _split_clauses(text)
        if not clauses:
            return
        context_id = uuid4().hex
        step = max(_SAMPLE_RATE_HZ * _BYTES_PER_SAMPLE * self._chunk_ms // 1000, _BYTES_PER_SAMPLE)
        buf = bytearray()
        seq = 0
        async with call_external_ws(self._ws_url(), service="cartesia", unit_cost_usd=0.0) as ws:
            for clause in clauses:
                await ws.send(json.dumps(self._ws_request(context_id, clause, cont=True)))
            await ws.send(json.dumps(self._ws_request(context_id, "", cont=False)))
            while True:
                raw = await ws.recv()
                message = json.loads(raw)
                if message.get("context_id") not in (None, context_id):
                    continue  # a stray frame from another context on the socket
                kind = message.get("type")
                if kind == "chunk":
                    data = message.get("data")
                    if data:
                        buf.extend(base64.b64decode(data))
                        while len(buf) >= step:
                            yield AudioChunk(pcm=bytes(buf[:step]), seq=seq)
                            seq += 1
                            del buf[:step]
                elif kind == "error":
                    raise RuntimeError(f"cartesia ws error: {message.get('error')}")
                elif kind == "done" or message.get("done"):
                    break
        # Final frame: the trailing remainder (or an empty end-marker) carries is_final so the
        # stream closes with exactly one final chunk, matching the REST path's contract.
        yield AudioChunk(pcm=bytes(buf), seq=seq, is_final=True)

    # -- REST fallback path ----------------------------------------------------------

    async def _stream_rest(self, text: str) -> AsyncIterator[AudioChunk]:
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
        """The sole raw Cartesia REST round-trip; invoked only via ``call_external`` (AC-XCUT-03).

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
