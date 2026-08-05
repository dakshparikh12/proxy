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
    OutputMediaChannel,
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


# ---------------------------------------------------------------------------
# 6. Backpressure: a draining page NEVER drops speech — a burst larger than the
#    old drop-oldest cap arrives intact, in order (the live-run choppiness fix).
# ---------------------------------------------------------------------------


class _GatedWebSocket:
    """A fake page WS whose ``send_bytes`` blocks until the test releases each frame — so the test
    controls the drain rate and can force the channel's buffer to fill (driving backpressure). Every
    frame it accepts is recorded in order, so the test can prove NOTHING was dropped."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self._go = asyncio.Event()  # set ⇒ drains freely; clear ⇒ each send awaits

    def hold(self) -> None:
        self._go.clear()

    def release(self) -> None:
        self._go.set()

    async def send_bytes(self, data: bytes) -> None:
        await self._go.wait()
        self.received.append(data)

    async def send_text(self, data: str) -> None:  # state frames ride the same gate
        await self._go.wait()
        self.received.append(data.encode())


@pytest.mark.asyncio
async def test_backpressure_burst_larger_than_cap_arrives_intact_in_order() -> None:
    """The choppiness fix: SpeakPipe dumps a whole sentence's PCM in one burst. With a page draining,
    a burst FAR larger than ``MAX_BUFFERED_FRAMES`` must arrive INTACT and IN ORDER — ``write_audio``
    awaits when the buffer is full (real backpressure) instead of silently dropping the oldest frames
    (the old ``deque(maxlen=...)`` behavior that ate mid-sentence audio the page never saw)."""
    channel = OutputMediaChannel("bp")
    ws = _GatedWebSocket()
    token = channel._attach()
    ws.hold()  # the page cannot drain yet → the buffer will fill and the writer must block
    pump = asyncio.ensure_future(channel._pump(token, ws))

    burst = MAX_BUFFERED_FRAMES * 3  # far past the old drop-oldest cap
    frames = [i.to_bytes(4, "big") for i in range(burst)]

    async def _write_all() -> None:
        for f in frames:
            await channel.write_audio(f)

    writer = asyncio.ensure_future(_write_all())
    # With the drain held, the writer must BLOCK on backpressure once the buffer is full — it cannot
    # have written the whole burst (proving no silent drop-oldest let it race ahead).
    await asyncio.sleep(0.05)
    assert not writer.done(), "the writer blocked on backpressure (did not burst past the cap)"
    assert channel._pcm_buffered <= MAX_BUFFERED_FRAMES

    ws.release()  # let the page drain — the writer unblocks and the whole burst flows
    await asyncio.wait_for(writer, timeout=5.0)
    # Drain fully, then stop the pump.
    async def _drained() -> bool:
        return channel._pcm_buffered == 0 and not channel._frames
    for _ in range(1000):
        if await _drained():
            break
        await asyncio.sleep(0.005)
    pump.cancel()
    await asyncio.gather(pump, return_exceptions=True)

    # EVERY frame arrived, in order — nothing dropped.
    assert ws.received == frames, "the full burst arrived intact and in order (no drops)"


@pytest.mark.asyncio
async def test_cut_while_writer_blocked_clears_and_unblocks() -> None:
    """Backpressure must NEVER deadlock a barge-in (Law 3): if the writer is blocked on a full buffer
    when a human talks over Proxy, ``cut`` must clear the buffered PCM AND release the blocked writer
    (which then falls through, muted/closed-safe) — the room goes silent at once, no hang."""
    channel = OutputMediaChannel("bpcut")
    ws = _GatedWebSocket()
    token = channel._attach()
    ws.hold()  # never drains → the writer will block on a full buffer
    pump = asyncio.ensure_future(channel._pump(token, ws))

    burst = MAX_BUFFERED_FRAMES * 3
    written = 0

    async def _write_all() -> None:
        nonlocal written
        for i in range(burst):
            await channel.write_audio(i.to_bytes(4, "big"))
            written += 1

    writer = asyncio.ensure_future(_write_all())
    await asyncio.sleep(0.05)
    assert not writer.done(), "the writer is blocked on backpressure"
    blocked_at = written

    # A human barges in: cut must clear the buffered PCM AND release the blocked writer so it makes
    # progress (no deadlock). The cut clears the buffer to 0, so the woken writer has room again.
    await channel.cut()
    await asyncio.sleep(0.05)
    assert channel._pcm_buffered <= MAX_BUFFERED_FRAMES  # cut cleared; the writer refills at most a cap
    assert written > blocked_at, "the writer progressed past the block after the cut (unblocked)"

    # Now release the drain so the whole thing finishes cleanly (proving no lingering deadlock).
    ws.release()
    await asyncio.wait_for(writer, timeout=5.0)  # would hang forever if cut/release didn't free it
    assert writer.done()
    pump.cancel()
    await asyncio.gather(pump, return_exceptions=True)
