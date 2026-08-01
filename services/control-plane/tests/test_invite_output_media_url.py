"""BUG 1 (make-or-break audio) — the Output-Media camera URL the bot joins with must carry THIS
meeting's id, matching the per-meeting page the speak path writes PCM into.

The served page is per-meeting (``GET /output-media/{meeting_id}`` + ``/output-media/{meeting_id}/ws``)
and the speak path writes PCM to ``channel_for(meeting_id)``. If the join body's
``output_media.camera.config.url`` has no meeting_id, the bot loads a page whose channel the speak
path never writes into → every frame is dropped → the bot is in the room but SILENT.

These prove the invite path threads the meeting_id into the camera URL, and that the URL matches the
route the page is served on.
"""
from __future__ import annotations

import asyncio
from typing import Any

from in_meeting import output_media


def _route_template() -> str:
    """The path template ``GET /output-media/{meeting_id}`` is served on (from the real router)."""
    for r in output_media.router.routes:
        path = getattr(r, "path", "")
        if path.endswith("/output-media/{meeting_id}"):
            return path
    raise AssertionError("output-media page route not found")


def test_default_transport_output_media_url_carries_the_meeting_id(monkeypatch: Any) -> None:
    """``_default_transport(meeting_id=...)`` builds a camera URL of the form the page is served on:
    ``<origin>/output-media/<meeting_id>`` — the meeting_id is in the path."""
    from control_plane.meetings import _default_transport

    monkeypatch.setenv("RECALL_OUTPUT_MEDIA_URL", "https://bot.example.com")
    monkeypatch.setenv("RECALL_WEBHOOK_URL", "https://hook.example/webhooks/recall")

    transport = _default_transport(meeting_id="m-XYZ")
    body = transport._join_body("https://meet.example/x")
    url = body["output_media"]["camera"]["config"]["url"]

    assert url == "https://bot.example.com/output-media/m-XYZ"
    # the URL path matches the served page route template (with the id substituted):
    assert url.endswith(_route_template().format(meeting_id="m-XYZ"))


def test_invite_proxy_joins_with_a_per_meeting_output_media_url(monkeypatch: Any) -> None:
    """End-to-end: ``invite_proxy`` (default transport) launches the bot with a camera URL that
    carries the created meeting's id — so the bot loads THE page whose channel the speak path feeds."""
    from control_plane import meetings

    monkeypatch.setenv("RECALL_API_KEY", "k")
    monkeypatch.setenv("RECALL_WEBHOOK_URL", "https://hook.example/webhooks/recall")
    monkeypatch.setenv("RECALL_OUTPUT_MEDIA_URL", "https://bot.example.com")

    meeting_id = "m-live-42"

    # A fake DB: insert_meeting RETURNs a row with our known id; update_bot_id RETURNs a row too.
    class _Conn:
        async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
            if "INSERT INTO meetings" in sql:
                return {
                    "id": meeting_id, "tenant_id": "t-1", "repo_id": "r-1",
                    "pinned_sha": "deadbeef", "recall_bot_id": None,
                    "status": "live", "platform": "recall",
                }
            # update_bot_id RETURNING row
            return {
                "id": meeting_id, "tenant_id": "t-1", "repo_id": "r-1",
                "pinned_sha": "deadbeef", "recall_bot_id": args[-1], "status": "live",
            }

    class _Acquire:
        async def __aenter__(self) -> Any:
            return _Conn()

        async def __aexit__(self, *exc: object) -> bool:
            return False

    class _DB:
        def acquire(self) -> Any:
            return _Acquire()

    # Capture the join body the real RecallTransport would POST (spy on the raw round-trip),
    # returning Recall's launched-bot id so the join succeeds.
    captured: dict[str, Any] = {}

    async def _fake_api(self: Any, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if method == "POST" and path == "/bot":
            captured["body"] = body
            return {"id": "recall-bot-live-1"}
        return {}  # post_chat (consent notice) etc.

    monkeypatch.setattr(meetings.RecallTransport, "_api", _fake_api)

    async def _run() -> None:
        invited = await meetings.invite_proxy(
            _DB(),  # type: ignore[arg-type]
            tenant_id="t-1", repo_id="r-1",
            meeting_url="https://meet.example/x", head_sha="deadbeef",
        )
        assert invited.recall_bot_id == "recall-bot-live-1"

    asyncio.run(_run())

    url = captured["body"]["output_media"]["camera"]["config"]["url"]
    assert url == f"https://bot.example.com/output-media/{meeting_id}"
    # and the join body labels the bot Proxy (BUG 3) with no fabricated participant_events (BUG 2):
    assert captured["body"]["bot_name"] == "Proxy"
    assert "participant_events" not in captured["body"]["recording_config"]
