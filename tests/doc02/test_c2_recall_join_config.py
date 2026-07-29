"""C2 — the ``join`` bot-create body carries Recall's FULL real config.

Regression for the starved inbound path: ``RecallTransport.join`` used to post only
``{"meeting_url": ...}``, so Recall never ran streaming transcription and never sent
transcript webhooks. This suite proves the body now carries, per Recall's REAL
create-bot schema (confirmed against the ``bot_create`` OpenAPI definition):

  (a) ``recording_config.transcript.provider.assembly_ai_v3_streaming`` — AssemblyAI
      Universal-Streaming, BYOK (the key is registered in Recall's dashboard and is
      NEVER sent per-call; the provider object rides empty),
  (b) ``output_media.camera`` = ``{kind: "webpage", config: {url}}`` — Recall's Output
      Media, the designated low-latency agent path that lets the bot emit Proxy's
      audio (Recall's ``output_audio`` clip endpoint is explicitly not for
      conversational audio),
  (c) ``recording_config.realtime_endpoints`` = one ``webhook`` endpoint at OUR
      receiver carrying ``transcript.data`` + ``transcript.partial_data`` +
      ``participant_events.chat_message`` — the exact event names Recall's schema
      enumerates and the harness drain consumes (CHAT-JOIN: without the chat event
      here, Recall never DELIVERS the chat webhooks the CUTOVER wired). Bot STATUS
      events cannot be subscribed per-bot (Recall delivers them only through the
      dashboard-configured webhook, verified by ``recall_webhook_secret``),
  (d) ``recording_config.participant_events`` = ``{}`` — the enabling block Recall's
      receiving-chat-messages guide names for participant-event capture; carried so
      chat delivery never silently depends on the recording artifact being live.

Deterministic: the ONLY fake is the httpx client at the ``http_client`` seam (the
C1 precedent); ``call_external`` is the real funnel. No live network call is
possible. Product imports live inside test bodies so collection stays clean.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


def _install_fake_http(
    monkeypatch: Any, *, payload: dict[str, Any], status: int = 200
) -> list[dict[str, Any]]:
    """Patch ``libs.http``'s ``http_client`` with a recording fake; return the wire log."""
    import libs.http.src.http.external as ext

    # Region hygiene: the URL assertions below pin the default https://api.recall.ai
    # host — the ambient env (e.g. a sourced .env with RECALL_REGION) must never leak in.
    monkeypatch.delenv("RECALL_REGION", raising=False)

    calls: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = status

        def raise_for_status(self) -> None:
            if status >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {status}", request=None, response=None  # type: ignore[arg-type]
                )

        def json(self) -> dict[str, Any]:
            return payload

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
            return _FakeResponse()

    monkeypatch.setattr(ext, "http_client", _FakeClient)
    return calls


# Recall's real create-bot response shape: the launched bot's unique id + status.
_RECALL_BOT_RESPONSE: dict[str, Any] = {
    "id": "7d1e2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b",
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "status_changes": [{"code": "joining_call"}],
}

_WEBHOOK_URL = "https://proxy.example.com/webhooks/recall"
_OUTPUT_MEDIA_URL = "https://proxy.example.com/meeting-surface"


def test_c2_join_body_carries_the_full_recall_config(monkeypatch: Any) -> None:
    """join → POST /bot whose body carries EXACTLY the real Recall config fields.

    Each of (a) streaming transcription, (b) output media, (c) realtime transcript
    delivery is asserted field-by-field with its exact value — this FAILS on the old
    ``{"meeting_url": ...}``-only body.
    """
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)
    transport = RecallTransport(
        call_external,
        api_key="rk_c2_acceptance_key",
        webhook_url=_WEBHOOK_URL,
        output_media_url=_OUTPUT_MEDIA_URL,
    )

    bot_id = asyncio.run(transport.join("https://meet.google.com/abc-defg-hij"))

    assert len(calls) == 1, f"expected exactly one wire round-trip, got {calls!r}"
    wire = calls[0]
    assert wire["method"] == "POST"
    assert wire["url"] == "https://api.recall.ai/api/v1/bot"
    body = wire["json"]

    # The meeting link still rides the body untouched.
    assert body["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    # (a) Streaming transcription: AssemblyAI Universal-Streaming (v3), BYOK — the
    # provider object is EXACTLY empty (our AssemblyAI key lives in Recall's
    # dashboard; it must never ride the request body).
    assert body["recording_config"]["transcript"]["provider"] == {
        "assembly_ai_v3_streaming": {}
    }

    # (c) Real-time delivery to OUR receiver: one webhook realtime endpoint with the
    # exact event names Recall's schema enumerates — transcript finals + partials
    # AND the meeting-chat event (the same names the harness drain consumes).
    assert body["recording_config"]["realtime_endpoints"] == [
        {
            "type": "webhook",
            "url": _WEBHOOK_URL,
            "events": [
                "transcript.data",
                "transcript.partial_data",
                "participant_events.chat_message",
            ],
        }
    ]

    # (d) Participant-event capture is explicitly enabled (empty block per Recall's
    # docs — no credential, no options) so chat rides even without a live recording.
    assert body["recording_config"]["participant_events"] == {}

    # (b) Output media: the bot streams OUR webpage as its camera — Recall's real
    # agent audio-output capability (kind 'webpage' is the only kind the schema
    # allows; config carries only the url).
    assert body["output_media"] == {
        "camera": {"kind": "webpage", "config": {"url": _OUTPUT_MEDIA_URL}}
    }

    # The id is EXACTLY what Recall returned — never fabricated.
    assert bot_id == "7d1e2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"


def test_c2_join_config_generalizes_to_any_link_and_endpoints(monkeypatch: Any) -> None:
    """The SAME config structure is built for ANY meeting link / receiver / surface
    URL — the values come from the inputs, never from constants baked into join."""
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    cases = [
        (
            "https://us02web.zoom.us/j/1234567890?pwd=abc",
            "https://tenant-a.proxy.dev/webhooks/recall",
            "https://tenant-a.proxy.dev/surface",
        ),
        (
            "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x",
            "https://other-host.example.org/webhooks/recall",
            "https://other-host.example.org/canvas",
        ),
    ]
    for meeting_link, webhook_url, output_media_url in cases:
        calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)
        transport = RecallTransport(
            call_external,
            api_key="rk_c2_acceptance_key",
            webhook_url=webhook_url,
            output_media_url=output_media_url,
        )

        asyncio.run(transport.join(meeting_link))

        body = calls[0]["json"]
        assert body["meeting_url"] == meeting_link
        assert body["recording_config"]["transcript"]["provider"] == {
            "assembly_ai_v3_streaming": {}
        }
        assert body["recording_config"]["realtime_endpoints"] == [
            {
                "type": "webhook",
                "url": webhook_url,
                "events": [
                    "transcript.data",
                    "transcript.partial_data",
                    "participant_events.chat_message",
                ],
            }
        ]
        assert body["recording_config"]["participant_events"] == {}
        assert body["output_media"]["camera"]["config"]["url"] == output_media_url


def test_c2_no_secret_ever_rides_the_join_body(monkeypatch: Any) -> None:
    """Secrets stay out of the body: the Recall key rides ONLY the auth header, and
    the AssemblyAI provider object carries no credential (BYOK — dashboard-side)."""
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)
    transport = RecallTransport(
        call_external,
        api_key="rk_c2_secret_key",
        webhook_url=_WEBHOOK_URL,
        output_media_url=_OUTPUT_MEDIA_URL,
    )

    asyncio.run(transport.join("https://meet.google.com/abc-defg-hij"))

    wire = calls[0]
    flat_body = json.dumps(wire["json"])
    assert "rk_c2_secret_key" not in flat_body, "the Recall API key must never ride the body"
    assert wire["headers"]["Authorization"] == "Token rk_c2_secret_key"
    # The AssemblyAI provider object is empty — no api key / token field of any kind.
    assert wire["json"]["recording_config"]["transcript"]["provider"][
        "assembly_ai_v3_streaming"
    ] == {}


def test_c2_unconfigured_receiver_ships_no_dangling_config(monkeypatch: Any) -> None:
    """With no receiver/surface configured, join ships NO recording_config and NO
    output_media — never an empty-string URL (Recall's schema rejects url: '').

    This is the C1 construction (api_key only): its body stays the minimal
    ``{"meeting_url": ...}``, so the C1 wire contract holds unchanged.
    """
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)
    transport = RecallTransport(call_external, api_key="rk_c2_acceptance_key")

    asyncio.run(transport.join("https://meet.google.com/abc-defg-hij"))

    assert calls[0]["json"] == {"meeting_url": "https://meet.google.com/abc-defg-hij"}


def test_c2_production_default_transport_feeds_the_join_config(monkeypatch: Any) -> None:
    """The REAL construction site (``control_plane.meetings._default_transport``) feeds the
    join config from the environment — production joins carry the full body, exactly
    the way this suite's mocked-seam joins do."""
    from control_plane.meetings import _default_transport

    monkeypatch.setenv("RECALL_API_KEY", "rk_c2_env_key")
    monkeypatch.setenv("RECALL_WEBHOOK_URL", "https://prod.proxy.example/webhooks/recall")
    monkeypatch.setenv("RECALL_OUTPUT_MEDIA_URL", "https://prod.proxy.example/surface")
    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)

    transport = _default_transport()
    bot_id = asyncio.run(transport.join("https://meet.google.com/env-fed-link"))

    wire = calls[0]
    assert wire["headers"]["Authorization"] == "Token rk_c2_env_key"
    body = wire["json"]
    assert body["meeting_url"] == "https://meet.google.com/env-fed-link"
    assert body["recording_config"]["transcript"]["provider"] == {
        "assembly_ai_v3_streaming": {}
    }
    assert body["recording_config"]["realtime_endpoints"] == [
        {
            "type": "webhook",
            "url": "https://prod.proxy.example/webhooks/recall",
            "events": [
                "transcript.data",
                "transcript.partial_data",
                "participant_events.chat_message",
            ],
        }
    ]
    assert body["recording_config"]["participant_events"] == {}
    assert body["output_media"] == {
        "camera": {
            "kind": "webpage",
            "config": {"url": "https://prod.proxy.example/surface"},
        }
    }
    assert bot_id == "7d1e2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"
