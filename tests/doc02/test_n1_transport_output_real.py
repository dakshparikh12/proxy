"""N1 — the Recall + Cartesia OUTPUT connections are REAL (C3/C5/C6/C8).

Regression for the stubbed vendor-output layer: ``_RecallOutputMedia.write_audio`` /
``write_frame`` used to route through a fake ``_send`` that returned a literal dict and
never touched HTTP, and ``CartesiaTTS._synth`` returned ``{"chunks": 0}`` so synthesis
yielded empty ``AudioChunk(pcm=b"")`` frames. This suite proves the layer now speaks the
REAL vendor shapes, confirmed against the live docs (no invented fields):

* C3 — audio out: POST ``/api/v1/bot/{id}/output_audio/`` with ``{"kind": "mp3",
  "b64_data": <base64 of the chunk's exact bytes>}`` — the only ``kind`` Recall's schema
  allows (https://docs.recall.ai/reference/bot_output_audio_create); flush rides Recall's
  real stop: DELETE ``/api/v1/bot/{id}/output_audio/`` → 204, no body
  (https://docs.recall.ai/reference/bot_output_audio_destroy).
* C5 — mute/unmute: Recall exposes NO direct bot-mute endpoint (confirmed across the
  output-audio docs, https://docs.recall.ai/docs/output-audio-in-meetings); the equivalent
  is the real stop call (DELETE output_audio, kills in-flight audio) plus output-audio
  suppression while muted; unmute lifts suppression and issues NO invented wire call.
* C6 — video out: POST ``/api/v1/bot/{id}/output_video/`` with ``{"kind": "jpeg",
  "b64_data": <base64 of the frame's exact bytes>}`` — the only ``kind`` allowed
  (https://docs.recall.ai/reference/bot_output_video_create).
* C8 — Cartesia Sonic-3 streaming synth: POST ``https://api.cartesia.ai/tts/bytes`` with
  ``Authorization: Bearer <key>`` + ``Cartesia-Version: 2026-03-01`` and the real body
  (``model_id``/``transcript``/``voice``/``output_format``), raw PCM ``pcm_s16le`` @
  16000 Hz mono — Recall's documented in-meeting PCM convention
  (https://docs.cartesia.ai/api-reference/tts/bytes); the streamed bytes come back framed
  as small non-empty ``AudioChunk``s, text verbatim, one fixed voice.

Deterministic: the ONLY fake is the httpx client at the ``http_client`` seam (the exact
C1/C2 boundary); ``call_external`` is the real funnel. No live network call is possible.
Product imports live inside test bodies so collection stays clean.
"""
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def _install_fake_http(
    monkeypatch: Any,
    *,
    payload: dict[str, Any] | None = None,
    content: bytes = b"",
    status: int = 200,
) -> list[dict[str, Any]]:
    """Patch ``libs.http``'s ``http_client`` with a recording fake; return the wire log.

    Stands in for the ``httpx.AsyncClient`` that ``http_client()`` constructs — records
    the exact wire facts (method / url / headers / json body) of every request and serves
    the canned response: ``payload`` for JSON round-trips (Recall), ``content`` streamed
    in parts for byte round-trips (Cartesia ``/tts/bytes``). A DELETE is answered 204
    with NO parsable body — exactly Recall's documented stop-audio response — so parsing
    a body on 204 fails loudly. Nothing else is faked.
    """
    import libs.http.src.http.external as ext

    # Region hygiene: the URL assertions below pin the default https://api.recall.ai
    # host — the ambient env (e.g. a sourced .env with RECALL_REGION) must never leak in.
    monkeypatch.delenv("RECALL_REGION", raising=False)

    calls: list[dict[str, Any]] = []

    class _FakeResponse:
        def __init__(self, method: str) -> None:
            self.status_code = 204 if method == "DELETE" else status

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}", request=None, response=None  # type: ignore[arg-type]
                )

        def json(self) -> dict[str, Any]:
            if self.status_code == 204:
                raise AssertionError("a 204 carries no body — _api must not parse one")
            return dict(payload or {})

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            # Serve the canned audio in several parts, as a chunked stream would.
            for i in range(0, len(content), 1024):
                yield content[i : i + 1024]

    class _FakeStream:
        def __init__(self, method: str) -> None:
            self._resp = _FakeResponse(method)

        async def __aenter__(self) -> _FakeResponse:
            return self._resp

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json: Any = None,
            **kwargs: Any,
        ) -> _FakeResponse:
            calls.append(
                {"method": method, "url": url, "headers": dict(headers or {}), "json": json}
            )
            return _FakeResponse(method)

        def stream(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json: Any = None,
            **kwargs: Any,
        ) -> _FakeStream:
            calls.append(
                {"method": method, "url": url, "headers": dict(headers or {}), "json": json}
            )
            return _FakeStream(method)

    monkeypatch.setattr(ext, "http_client", _FakeClient)
    return calls


async def _drain(chunks: AsyncIterator[Any]) -> list[Any]:
    return [c async for c in chunks]


# ── C3 · real audio out ──────────────────────────────────────────────────────────────


def test_c3_write_audio_sends_the_chunks_exact_bytes_via_api(monkeypatch: Any) -> None:
    """write_audio → POST /bot/{id}/output_audio/ carrying the chunk's EXACT bytes
    base64'd under Recall's real ``b64_data`` field with the only allowed ``kind``."""
    from libs.http.src.http.external import call_external
    from transport.media import AudioChunk
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_n1_key")
    sink = transport.output_media("bot-42")

    pcm = bytes(range(256)) * 3  # arbitrary real byte payload — not a seq/final echo
    asyncio.run(sink.write_audio(AudioChunk(pcm=pcm, seq=0, is_final=True)))

    assert len(calls) == 1, f"expected exactly one wire round-trip, got {calls!r}"
    wire = calls[0]
    assert wire["method"] == "POST"
    assert wire["url"] == "https://api.recall.ai/api/v1/bot/bot-42/output_audio/"
    assert wire["headers"]["Authorization"] == "Token rk_n1_key"
    # Recall's real body: exactly {kind, b64_data} — never {"seq", "final"}.
    assert set(wire["json"]) == {"kind", "b64_data"}
    assert wire["json"]["kind"] == "mp3"  # the ONLY kind Recall's schema allows
    assert base64.b64decode(wire["json"]["b64_data"]) == pcm, (
        "b64_data must decode back to the chunk's exact bytes"
    )


def test_c3_flush_issues_recalls_real_stop_audio_call(monkeypatch: Any) -> None:
    """flush → DELETE /bot/{id}/output_audio/ (Recall's real stop; 204, no body)."""
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_n1_key")
    sink = transport.output_media("bot-42")

    asyncio.run(sink.flush())

    assert [(c["method"], c["url"]) for c in calls] == [
        ("DELETE", "https://api.recall.ai/api/v1/bot/bot-42/output_audio/"),
    ]
    assert calls[0]["headers"]["Authorization"] == "Token rk_n1_key"


def test_c3_c6_the_fake_send_path_is_gone() -> None:
    """The fabricated ``_send`` dict-echo path no longer exists on the sink."""
    from transport.recall import _RecallOutputMedia

    assert not hasattr(_RecallOutputMedia, "_send"), (
        "the fake _send path must be deleted — all output rides the real _api round-trip"
    )


# ── C5 · mute / unmute ───────────────────────────────────────────────────────────────


def test_c5_mute_issues_the_real_stop_and_suppresses_audio_out(monkeypatch: Any) -> None:
    """mute → the real DELETE output_audio stop (kills in-flight audio), then every
    write_audio is suppressed (zero wire calls) until unmute."""
    from libs.http.src.http.external import call_external
    from transport.media import AudioChunk
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_n1_key")
    sink = transport.output_media("bot-42")  # created BEFORE mute — must observe it live

    async def _drive() -> None:
        await transport.mute("bot-42")
        await sink.write_audio(AudioChunk(pcm=b"\x01\x02", seq=0))
        await sink.write_audio(AudioChunk(pcm=b"\x03\x04", seq=1, is_final=True))

    asyncio.run(_drive())

    # Exactly ONE wire call: the real-shaped stop. The muted writes never hit the wire.
    assert [(c["method"], c["url"]) for c in calls] == [
        ("DELETE", "https://api.recall.ai/api/v1/bot/bot-42/output_audio/"),
    ]
    assert calls[0]["headers"]["Authorization"] == "Token rk_n1_key"


def test_c5_unmute_lifts_suppression_without_an_invented_call(monkeypatch: Any) -> None:
    """unmute lifts suppression so audio rides again — and issues NO wire call itself
    (Recall has no unmute endpoint; posting new audio is how output resumes)."""
    from libs.http.src.http.external import call_external
    from transport.media import AudioChunk
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_n1_key")
    sink = transport.output_media("bot-42")
    pcm = b"\x05\x06\x07\x08"

    async def _drive() -> None:
        await transport.mute("bot-42")
        await sink.write_audio(AudioChunk(pcm=b"suppressed", seq=0))
        await transport.unmute("bot-42")
        await sink.write_audio(AudioChunk(pcm=pcm, seq=1, is_final=True))

    asyncio.run(_drive())

    # mute's DELETE, then ONLY the post-unmute audio POST — nothing for unmute itself.
    assert [(c["method"], c["url"]) for c in calls] == [
        ("DELETE", "https://api.recall.ai/api/v1/bot/bot-42/output_audio/"),
        ("POST", "https://api.recall.ai/api/v1/bot/bot-42/output_audio/"),
    ]
    assert base64.b64decode(calls[1]["json"]["b64_data"]) == pcm
    # Mute state is per-bot: another bot's sink is never suppressed by bot-42's mute.
    other = transport.output_media("bot-77")
    asyncio.run(transport.mute("bot-42"))
    asyncio.run(other.write_audio(AudioChunk(pcm=b"\x09", seq=0, is_final=True)))
    assert calls[-1]["url"] == "https://api.recall.ai/api/v1/bot/bot-77/output_audio/"


# ── C6 · real video/frame out ────────────────────────────────────────────────────────


def test_c6_write_frame_sends_the_frames_exact_bytes_via_api(monkeypatch: Any) -> None:
    """write_frame → POST /bot/{id}/output_video/ carrying the frame's EXACT bytes
    base64'd under Recall's real ``b64_data`` field with the only allowed ``kind``."""
    from libs.http.src.http.external import call_external
    from transport.media import CanvasFrame
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_n1_key")
    sink = transport.output_media("bot-42")

    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(64)) + b"\xff\xd9"  # JPEG-framed payload
    frame = CanvasFrame(data=jpeg, width=1280, height=720, seq=7, surface="screen")
    asyncio.run(sink.write_frame(frame))

    assert len(calls) == 1, f"expected exactly one wire round-trip, got {calls!r}"
    wire = calls[0]
    assert wire["method"] == "POST"
    assert wire["url"] == "https://api.recall.ai/api/v1/bot/bot-42/output_video/"
    assert wire["headers"]["Authorization"] == "Token rk_n1_key"
    # Recall's real body: exactly {kind, b64_data} — never a {"surface"} echo.
    assert set(wire["json"]) == {"kind", "b64_data"}
    assert wire["json"]["kind"] == "jpeg"  # the ONLY kind Recall's schema allows
    assert base64.b64decode(wire["json"]["b64_data"]) == jpeg, (
        "b64_data must decode back to the frame's exact bytes"
    )


# ── C8 · real Cartesia Sonic-3 streaming synth ───────────────────────────────────────

# ~375ms of 16 kHz mono s16le PCM — enough for several 120ms chunks + a short tail.
_SYNTH_PCM = bytes(range(256)) * 47  # 12032 bytes, non-trivial content


def test_c8_synthesize_streams_real_pcm_chunks(monkeypatch: Any) -> None:
    """text in → real Cartesia round-trip → non-empty AudioChunk frames (chunks > 0)
    that reassemble byte-exactly, framed ≤ tts_chunk_ms of 16 kHz s16le PCM."""
    from libs.http.src.http.external import call_external
    from transport.tts import CartesiaTTS

    calls = _install_fake_http(monkeypatch, content=_SYNTH_PCM)
    tts = CartesiaTTS(
        call_external, api_key="ck_n1_key", chunk_ms=120, voice_id="voice-fixed-1"
    )

    chunks = asyncio.run(_drain(tts.synthesize("p95 is 340ms")))

    # Real framed audio: chunks > 0, every chunk carries real bytes (never pcm=b"").
    assert len(chunks) > 1, f"expected multiple real chunks, got {len(chunks)}"
    assert all(c.pcm for c in chunks), "no chunk may be empty — the chunks:0 stub is dead"
    assert b"".join(c.pcm for c in chunks) == _SYNTH_PCM, (
        "the framed chunks must reassemble to the exact synthesized bytes"
    )
    # Small-chunk bound: ≤ chunk_ms of 16 kHz mono s16le (16000 Hz × 2 B × 120 ms).
    frame_cap = 16000 * 2 * 120 // 1000
    assert all(len(c.pcm) <= frame_cap for c in chunks)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert [c.is_final for c in chunks] == [False] * (len(chunks) - 1) + [True]

    # The wire request is EXACTLY Cartesia's real /tts/bytes shape.
    assert len(calls) == 1
    wire = calls[0]
    assert wire["method"] == "POST"
    assert wire["url"] == "https://api.cartesia.ai/tts/bytes"
    assert wire["headers"]["Authorization"] == "Bearer ck_n1_key"
    assert wire["headers"]["Cartesia-Version"] == "2026-03-01"
    body = wire["json"]
    assert body["model_id"] == "sonic-3"
    assert body["transcript"] == "p95 is 340ms"
    assert body["voice"] == {"mode": "id", "id": "voice-fixed-1"}
    assert body["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }


def test_c8_transcript_rides_verbatim(monkeypatch: Any) -> None:
    """The exact text — unicode, punctuation, spacing — rides ``transcript`` untouched
    (AC-SPEAK-01: no headline extraction, no substitution)."""
    from libs.http.src.http.external import call_external
    from transport.tts import CartesiaTTS

    text = "p95 är 340 ms — «within budget», ok?  \n(two spaces kept)"
    calls = _install_fake_http(monkeypatch, content=_SYNTH_PCM)
    tts = CartesiaTTS(call_external, api_key="ck_n1_key", chunk_ms=120)

    asyncio.run(_drain(tts.synthesize(text)))

    assert calls[0]["json"]["transcript"] == text


def test_c8_api_key_comes_from_the_settings_surface_and_never_rides_the_body(
    monkeypatch: Any,
) -> None:
    """With no key injected, CartesiaTTS reads CARTESIA_API_KEY from the env settings
    surface; the key rides ONLY the Authorization header, never the JSON body."""
    from libs.http.src.http.external import call_external
    from transport.tts import CartesiaTTS

    monkeypatch.setenv("CARTESIA_API_KEY", "ck_from_env_secret")
    calls = _install_fake_http(monkeypatch, content=_SYNTH_PCM)
    tts = CartesiaTTS(call_external, chunk_ms=120)

    asyncio.run(_drain(tts.synthesize("hello")))

    wire = calls[0]
    assert wire["headers"]["Authorization"] == "Bearer ck_from_env_secret"
    assert "ck_from_env_secret" not in json.dumps(wire["json"]), (
        "the Cartesia key must never ride the request body"
    )


def test_c8_one_fixed_voice_rides_every_request(monkeypatch: Any) -> None:
    """The single configured voice rides EVERY synthesis identically (AC-SPEAK-02)."""
    from libs.http.src.http.external import call_external
    from transport.tts import CartesiaTTS

    calls = _install_fake_http(monkeypatch, content=_SYNTH_PCM)
    tts = CartesiaTTS(call_external, api_key="ck_n1_key", chunk_ms=120, voice_id="v-one")

    asyncio.run(_drain(tts.synthesize("first line")))
    asyncio.run(_drain(tts.synthesize("second line")))

    voices = [c["json"]["voice"] for c in calls]
    assert voices == [{"mode": "id", "id": "v-one"}, {"mode": "id", "id": "v-one"}]
