"""C1 — ``RecallTransport._api`` is a REAL HTTP round-trip through the libs.http seam.

Regression for the fabricated-dict stub: ``_api`` used to return a literal
``{"method", "url", "body"}`` payload and synthesize a fake ``recall-<uuid>`` id for
``POST /bot`` — it never touched the network and never placed the Recall key on a
request. This suite proves the mechanism is now a genuine round-trip:

  the REAL ``libs.http.call_external`` funnel invokes ``_api`` → ``_api`` builds the
  request against a client constructed ONLY by ``libs.http.http_client`` (the sole
  raw-client home, AC-XCUT-03) → exact method + ``https://api.recall.ai/api/v1``
  URL + ``Authorization: Token <key>`` (Recall's real auth scheme) + JSON body go
  out on the wire → the PARSED RESPONSE BODY comes back, never a fabricated dict.

Deterministic: the ONLY fake is the httpx client at the ``http_client`` seam (the
exact boundary the premeeting github_auth tests fake); ``call_external`` is the real
funnel. No live network call is possible. Product imports live inside test bodies so
collection stays clean.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx


def _install_fake_http(
    monkeypatch: Any, *, payload: dict[str, Any], status: int = 200
) -> list[dict[str, Any]]:
    """Patch ``libs.http``'s ``http_client`` with a recording fake; return the wire log.

    The fake stands in for the ``httpx.AsyncClient`` that ``http_client()`` constructs:
    it records the exact wire facts (method / url / headers / json body) of every
    request and serves the canned Recall response. ``_api`` imports ``http_client``
    lazily from ``libs.http.src.http.external``, so patching that module intercepts
    the round-trip at exactly the raw-client construction seam — nothing else is faked.
    """
    import libs.http.src.http.external as ext

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

        # The wire facts are what matter, not the client call spelling — accept the
        # verb-method forms httpx exposes as well.
        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return await self.request("POST", url, **kwargs)

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return await self.request("GET", url, **kwargs)

    monkeypatch.setattr(ext, "http_client", _FakeClient)
    return calls


# Recall's real create-bot response shape: the launched bot's unique id + status.
_RECALL_BOT_RESPONSE: dict[str, Any] = {
    "id": "bd8f4d54-9a2c-4b3e-8f1d-2e7c6a5b4d3c",
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "status_changes": [{"code": "joining_call"}],
}


def test_c1_api_post_bot_is_a_real_recall_round_trip(monkeypatch: Any) -> None:
    """join → real call_external funnel → _api issues EXACTLY the real Recall request.

    POST https://api.recall.ai/api/v1/bot, ``Authorization: Token <key>``, the given
    JSON body — and the bot id returned is the one RECALL sent back, never fabricated.
    """
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)
    transport = RecallTransport(call_external, api_key="rk_c1_acceptance_key")

    bot_id = asyncio.run(transport.join("https://meet.google.com/abc-defg-hij"))

    # Exactly one wire request, with the exact real method / URL / auth header / body.
    assert len(calls) == 1, f"expected exactly one wire round-trip, got {calls!r}"
    wire = calls[0]
    assert wire["method"] == "POST"
    assert wire["url"] == "https://api.recall.ai/api/v1/bot"
    assert wire["headers"]["Authorization"] == "Token rk_c1_acceptance_key"
    assert wire["json"] == {"meeting_url": "https://meet.google.com/abc-defg-hij"}

    # The id is EXACTLY what Recall returned — no recall-<uuid> fabrication.
    assert bot_id == "bd8f4d54-9a2c-4b3e-8f1d-2e7c6a5b4d3c"
    assert not re.match(r"^recall-[0-9a-f]{32}$", bot_id), (
        "_api must never fabricate a recall-<uuid> id"
    )


def test_c1_api_returns_the_parsed_response_body_exactly(monkeypatch: Any) -> None:
    """_api("POST", "/bot", {...}) returns the mock's parsed JSON body — nothing else.

    Dict equality proves no synthesized keys (no "method"/"url"/"body" echo, no
    injected id): the return IS ``resp.json()``.
    """
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload=_RECALL_BOT_RESPONSE)

    async def _passthrough_seam(op: Any, *, service: str, unit_cost_usd: float = 0.0) -> Any:
        return await op()

    transport = RecallTransport(_passthrough_seam, api_key="rk_c1_acceptance_key")
    body = {"meeting_url": "https://meet.google.com/abc-defg-hij"}

    result = asyncio.run(transport._api("POST", "/bot", body))

    assert result == _RECALL_BOT_RESPONSE, (
        "_api must return exactly the parsed response body, not a fabricated dict"
    )
    assert calls[0]["json"] == body
    assert calls[0]["headers"]["Authorization"] == "Token rk_c1_acceptance_key"


def test_c1_every_api_caller_flows_through_the_real_transport(monkeypatch: Any) -> None:
    """leave / post_chat / send_dm still call _api — and now hit the real wire unchanged."""
    from libs.http.src.http.external import call_external
    from transport.recall import RecallTransport

    calls = _install_fake_http(monkeypatch, payload={"ok": True})
    transport = RecallTransport(call_external, api_key="rk_c1_acceptance_key")

    async def _drive() -> None:
        await transport.leave("bot-77")
        await transport.post_chat("bot-77", "consent notice", pinned=True)
        await transport.send_dm("bot-77", "hello", "participant-9")

    asyncio.run(_drive())

    assert [(c["method"], c["url"]) for c in calls] == [
        ("POST", "https://api.recall.ai/api/v1/bot/bot-77/leave"),
        ("POST", "https://api.recall.ai/api/v1/bot/bot-77/chat"),
        ("POST", "https://api.recall.ai/api/v1/bot/bot-77/chat"),
    ]
    assert calls[1]["json"] == {"message": "consent notice", "pinned": True}
    assert calls[2]["json"] == {"message": "hello", "to": "participant-9"}
    # Every round-trip carries the real auth header; the key never mutates per call.
    assert all(c["headers"]["Authorization"] == "Token rk_c1_acceptance_key" for c in calls)


def test_c1_non_2xx_is_an_error_never_a_silent_success(monkeypatch: Any) -> None:
    """A 4xx/5xx from Recall surfaces as an error from _api — never a parsed 'success'."""
    import pytest

    from transport.recall import RecallTransport

    _install_fake_http(monkeypatch, payload={"detail": "invalid token"}, status=401)

    async def _passthrough_seam(op: Any, *, service: str, unit_cost_usd: float = 0.0) -> Any:
        return await op()

    transport = RecallTransport(_passthrough_seam, api_key="rk_wrong_key")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(transport._api("POST", "/bot", {"meeting_url": "https://meet.google.com/x"}))
