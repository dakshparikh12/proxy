"""The Recall create-bot join body — the shape that decides whether a bot JOINS, is HEARD, and
does NOT self-wake (first-live-meeting blockers).

* BUG 2: the fabricated ``recording_config.participant_events: {}`` key is gone (it could make
  Recall 400 the create-bot → no bot joins), while chat is STILL subscribed via the
  ``participant_events.chat_message`` string in the realtime endpoint's events.
* BUG 3: the body carries ``bot_name: "Proxy"`` so Recall labels the bot's own transcribed speech
  "Proxy" — aligning with ``meeting_session.PROXY_SPEAKER`` so the bot never self-wakes on its own
  "proxy"-containing speech (a billing loop).
"""
from __future__ import annotations

from typing import Any


def _transport(*, webhook: str = "https://hook.example/webhooks/recall",
               media: str = "") -> Any:
    from transport.recall import RecallTransport

    async def _noop_call(thunk: Any, **_: Any) -> Any:  # pragma: no cover - not exercised here
        return await thunk()

    return RecallTransport(_noop_call, api_key="k", webhook_url=webhook, output_media_url=media)


def test_join_body_has_no_top_level_participant_events_but_keeps_chat_subscription() -> None:
    """BUG 2: no ``recording_config.participant_events`` key (the fabricated ``{}`` that could 400
    the create-bot), yet ``participant_events.chat_message`` is still in the realtime events so chat
    is delivered."""
    body = _transport()._join_body("https://meet.example/x")

    rc = body["recording_config"]
    assert "participant_events" not in rc  # the fabricated key is gone

    events = rc["realtime_endpoints"][0]["events"]
    assert "participant_events.chat_message" in events  # chat is still subscribed
    assert "transcript.data" in events and "transcript.partial_data" in events


def test_join_body_sets_bot_name_proxy_for_the_self_wake_guard() -> None:
    """BUG 3: the join body labels the bot 'Proxy' so Recall tags the bot's own lines with that
    name — matching ``PROXY_SPEAKER`` so ``is_addressed`` filters Proxy's own speech (no self-wake)."""
    from control_plane.meeting_session import PROXY_SPEAKER

    body = _transport(media="https://media.example/output-media/m-1")._join_body("https://meet/x")
    assert body["bot_name"] == "Proxy"
    assert body["bot_name"] == PROXY_SPEAKER  # the label aligns with the self-wake guard


def test_join_body_carries_bot_name_even_without_a_webhook() -> None:
    """``bot_name`` rides regardless of the optional recording/output-media config — the self-wake
    label is never conditional on a configured receiver."""
    body = _transport(webhook="", media="")._join_body("https://meet/x")
    assert body["bot_name"] == "Proxy"
    assert "recording_config" not in body  # no receiver configured → no recording block
