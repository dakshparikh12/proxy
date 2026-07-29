"""Acceptance battery for OUTPUT-MEDIA — the Recall Output Media webpage, the
per-meeting audio channel, and the orb (``in_meeting.output_media``).

Recall bots join with ``output_media.camera = {kind: "webpage", config: {url}}``:
Recall loads that URL in a headless browser and streams the PAGE as the bot's
camera + audio into the meeting. This module is that page (the orb — SPEC §7:
one circle, no state machine) plus the server-side channel the speak path
writes PCM into.

AC[det], five criteria:
  1. GET /output-media/{id} → 200 text/html, contains the orb + the WS URL for
     that meeting, and NO internal component names (user-visible surface).
  2. WS round-trip: PCM written into the channel arrives as a BINARY frame;
     ``set_speaking`` arrives as the ``{"speaking": bool}`` JSON text frame.
  3. Channel isolation: audio written to m1 never reaches a page attached to m2.
  4. Bounded queue: with no page attached, writing more than the buffer cap
     neither blocks nor raises (oldest dropped); a late page gets the retained
     tail.
  5. Disconnect tolerance: a page going away is normal churn — writes keep
     working, and a reconnecting page receives audio again.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from in_meeting.output_media import (
    MAX_BUFFERED_FRAMES,
    build_output_media_router,
    channel_for,
    close_channel,
)

_MEETING_IDS = ("m1", "m2", "m3", "m4", "m5")


@pytest.fixture(autouse=True)
def _fresh_channels() -> Iterator[None]:
    """Each test starts and ends with a clean registry for the ids it uses."""
    for meeting_id in _MEETING_IDS:
        close_channel(meeting_id)
    yield
    for meeting_id in _MEETING_IDS:
        close_channel(meeting_id)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_output_media_router())
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. The page: orb + WS URL, and a user-visible surface free of internal names
# ---------------------------------------------------------------------------


def test_page_serves_orb_and_ws_url_with_no_internal_names(client: TestClient) -> None:
    resp = client.get("/output-media/m1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # The orb element is present (one circle — presence, not a state machine).
    assert 'id="orb"' in body
    # The page knows the WS endpoint for THIS meeting.
    assert "/output-media/m1/ws" in body
    # Naming law: a user-visible string never carries an internal component name.
    lowered = body.lower()
    for forbidden in ("harness", "workroom", "scribe"):
        assert forbidden not in lowered, f"internal name {forbidden!r} leaked into the page"


# ---------------------------------------------------------------------------
# 2. WS round-trip: binary PCM frames + the speaking JSON state frame
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_round_trip_audio_and_speaking(client: TestClient) -> None:
    pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    with client.websocket_connect("/output-media/m1/ws") as ws:
        await channel_for("m1").write_audio(pcm)
        assert ws.receive_bytes() == pcm
        await channel_for("m1").set_speaking(True)
        assert json.loads(ws.receive_text()) == {"speaking": True}
        await channel_for("m1").set_speaking(False)
        assert json.loads(ws.receive_text()) == {"speaking": False}


# ---------------------------------------------------------------------------
# 3. Channel isolation: one meeting's audio never crosses to another's page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_isolation(client: TestClient) -> None:
    with (
        client.websocket_connect("/output-media/m1/ws") as ws1,
        client.websocket_connect("/output-media/m2/ws") as ws2,
    ):
        # Write to m1 FIRST: if it leaked into m2, it would arrive as m2's
        # first frame ahead of m2's own audio — the ordered assert catches it.
        await channel_for("m1").write_audio(b"audio-for-m1")
        await channel_for("m2").write_audio(b"audio-for-m2")
        assert ws2.receive_bytes() == b"audio-for-m2"
        assert ws1.receive_bytes() == b"audio-for-m1"


# ---------------------------------------------------------------------------
# 4. Bounded queue: no page attached — never blocks, never raises, drops oldest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_queue_drops_oldest_without_blocking(client: TestClient) -> None:
    channel = channel_for("m3")
    assert channel.connected() is False
    overflow = 40
    total = MAX_BUFFERED_FRAMES + overflow
    for i in range(total):
        # wait_for proves each write completes promptly even past the cap.
        await asyncio.wait_for(channel.write_audio(i.to_bytes(4, "big")), timeout=1.0)
    assert channel.connected() is False
    with client.websocket_connect("/output-media/m3/ws") as ws:
        frames = [ws.receive_bytes() for _ in range(MAX_BUFFERED_FRAMES)]
    # Oldest dropped: the retained tail starts at `overflow`, ends at total-1.
    assert frames[0] == overflow.to_bytes(4, "big")
    assert frames[-1] == (total - 1).to_bytes(4, "big")


# ---------------------------------------------------------------------------
# 5. Disconnect tolerance: page churn never breaks the speak path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_then_write_then_reconnect(client: TestClient) -> None:
    with client.websocket_connect("/output-media/m4/ws") as ws:
        await channel_for("m4").write_audio(b"first")
        assert ws.receive_bytes() == b"first"
    # The page is gone. Writing must not raise — the channel stays (the page
    # may reconnect) and buffers what it can.
    await channel_for("m4").write_audio(b"while-away")
    with client.websocket_connect("/output-media/m4/ws") as ws:
        await channel_for("m4").write_audio(b"fresh")
        assert ws.receive_bytes() == b"while-away"
        assert ws.receive_bytes() == b"fresh"
