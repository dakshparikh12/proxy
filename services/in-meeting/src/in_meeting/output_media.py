"""OUTPUT-MEDIA — the Recall Output Media webpage, the per-meeting audio
channel, and the orb.

Recall bots join with ``output_media.camera = {kind: "webpage", config: {url}}``
(``RECALL_OUTPUT_MEDIA_URL`` on the join path): Recall loads that URL in a
headless browser and streams the PAGE — its canvas and its audio — into the
meeting as the bot's camera + microphone. This module is that missing surface:

* ``OutputMediaChannel`` — the server-side handle the speak path writes into:
  ``write_audio(pcm)`` enqueues raw PCM (s16le, 16 kHz, mono — the format the
  speech synth emits) and ``set_speaking(bool)`` drives the orb pulse.
* ``build_output_media_router()`` / ``router`` — the FastAPI router serving
  ``GET /output-media/{meeting_id}`` (the orb page, one inline HTML string —
  no build step, no external assets) and ``WS /output-media/{meeting_id}/ws``
  (the page's feed: PCM as BINARY frames, ``{"speaking": bool}`` as JSON text).
* ``channel_for(meeting_id)`` / ``close_channel(meeting_id)`` — the registry:
  one channel per meeting.

Wire-in hooks (this module only builds + exports; nothing here self-mounts):

* The control-plane server mounts the router::

      from in_meeting import output_media
      app.include_router(output_media.router)

* The speak sink pushes synthesized audio and the pulse signal::

      channel = output_media.channel_for(meeting_id)
      await channel.set_speaking(True)
      await channel.write_audio(pcm_chunk)   # per chunk, in order
      await channel.set_speaking(False)

Backpressure contract: the outbound buffer is BOUNDED (``MAX_BUFFERED_FRAMES``).
On overflow the OLDEST frame is dropped — live audio must never stall the speak
path, and a dead page must never grow memory unboundedly. A page that attaches
late receives the retained tail. A page disconnect keeps the channel (the page
may reconnect); ``close_channel`` is the deliberate end-of-meeting teardown.

Dependency note: ``fastapi``/``starlette`` are resolved from the workspace venv
(already a member dependency elsewhere); declaring them in this package's own
metadata is a tracked, consolidated cleanup.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Final

from fastapi import APIRouter, WebSocket
from fastapi.responses import HTMLResponse

__all__ = [
    "MAX_BUFFERED_FRAMES",
    "OutputMediaChannel",
    "build_output_media_router",
    "channel_for",
    "close_channel",
    "router",
]

#: Cap on buffered outbound frames per meeting. At ~100 ms of 16 kHz s16le per
#: chunk, 256 frames is ~25 s of audio — ample for a page reconnect window,
#: bounded against a page that never comes back.
MAX_BUFFERED_FRAMES: Final[int] = 256

#: The PCM sample rate the page's audio pipeline is built around (s16le, mono).
SAMPLE_RATE_HZ: Final[int] = 16_000


class OutputMediaChannel:
    """The per-meeting pipe between the speak path and the attached page.

    Frames are ``bytes`` (a raw PCM chunk → sent as a BINARY WS message) or
    ``str`` (a JSON state message → sent as a text WS message), kept in ONE
    ordered, bounded deque so speaking-state flips stay ordered relative to
    the audio around them. ``deque(maxlen=...)`` gives the drop-OLDEST
    overflow behavior atomically.

    Producers (``write_audio`` / ``set_speaking``) never block and never
    raise; they may run on a different event loop than the attached page's
    WS handler (the wake-up crosses loops via ``call_soon_threadsafe``).
    """

    def __init__(self, meeting_id: str, maxsize: int = MAX_BUFFERED_FRAMES) -> None:
        self.meeting_id = meeting_id
        self._frames: deque[bytes | str] = deque(maxlen=maxsize)
        self._attachment: object | None = None
        self._consumer_loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._closed = False

    # -- the speak-path surface ---------------------------------------------

    async def write_audio(self, pcm: bytes) -> None:
        """Enqueue one raw PCM chunk (s16le, 16 kHz, mono) for the page."""
        self._frames.append(pcm)
        self._notify()

    async def set_speaking(self, speaking: bool) -> None:
        """Signal the orb pulse: True while Proxy speaks, False after."""
        self._frames.append(json.dumps({"speaking": speaking}))
        self._notify()

    def connected(self) -> bool:
        """Is a page currently attached to this channel?"""
        return self._attachment is not None

    # -- internal machinery ---------------------------------------------------

    def _notify(self) -> None:
        """Wake the attached page's drain loop, from any loop or none."""
        loop, wake = self._consumer_loop, self._wake
        if loop is None or wake is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            # The consumer's loop is already gone — the page is churn; the
            # frames stay buffered for a reconnect.
            pass

    def _attach(self) -> object:
        """Attach the calling WS handler as THE page; latest attach wins."""
        superseded = self._wake
        token: object = object()
        self._attachment = token
        self._consumer_loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        wake.set()  # deliver anything already buffered immediately
        self._wake = wake
        if superseded is not None:
            superseded.set()  # release a stale pump so it can notice and exit
        return token

    def _detach(self, token: object) -> None:
        """Detach, but only if `token` is still the current attachment."""
        if self._attachment is token:
            self._attachment = None
            self._consumer_loop = None
            self._wake = None

    def _close(self) -> None:
        self._closed = True
        self._frames.clear()
        wake = self._wake
        if wake is not None:
            wake.set()

    async def _pump(self, token: object, websocket: WebSocket) -> None:
        """Drain frames to the attached page until superseded, closed, or dead."""
        while self._attachment is token and not self._closed:
            while self._frames and self._attachment is token and not self._closed:
                frame = self._frames.popleft()
                try:
                    if isinstance(frame, bytes):
                        await websocket.send_bytes(frame)
                    else:
                        await websocket.send_text(frame)
                except BaseException:
                    # Send failed (or the handler was cancelled) mid-frame:
                    # put the frame back so a reconnecting page still gets it.
                    self._frames.appendleft(frame)
                    raise
            wake = self._wake
            if wake is None or self._attachment is not token or self._closed:
                return
            wake.clear()
            if self._frames:
                continue  # a frame landed between the drain and the clear
            await wake.wait()


_channels: dict[str, OutputMediaChannel] = {}


def channel_for(meeting_id: str) -> OutputMediaChannel:
    """The one channel for this meeting (created on first use)."""
    channel = _channels.get(meeting_id)
    if channel is None:
        channel = OutputMediaChannel(meeting_id)
        _channels[meeting_id] = channel
    return channel


def close_channel(meeting_id: str) -> None:
    """End-of-meeting teardown: drop the channel and release any attached page."""
    channel = _channels.pop(meeting_id, None)
    if channel is not None:
        channel._close()


# ---------------------------------------------------------------------------
# The page: one inline HTML string. THE ORB — one circle on a dark background,
# presence only; a subtle pulse while speaking. No state machine, no assets,
# no build step: Recall's headless browser renders it standalone.
# ---------------------------------------------------------------------------

_PAGE_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Proxy</title>
<style>
  html, body { margin: 0; height: 100%; background: #0b0f14; overflow: hidden; }
  #stage { height: 100%; display: flex; align-items: center; justify-content: center; }
  #orb {
    width: 34vmin; height: 34vmin; border-radius: 50%;
    background: radial-gradient(circle at 38% 35%, #9ecbff 0%, #3f7bd9 45%, #16233c 100%);
    box-shadow: 0 0 8vmin rgba(90, 150, 240, 0.35);
  }
  #orb.speaking { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse {
    0%, 100% { transform: scale(1); box-shadow: 0 0 8vmin rgba(90, 150, 240, 0.35); }
    50% { transform: scale(1.06); box-shadow: 0 0 12vmin rgba(120, 175, 255, 0.55); }
  }
</style>
</head>
<body>
<div id="stage"><div id="orb"></div></div>
<script>
"use strict";
const WS_PATH = __WS_PATH__;
const SAMPLE_RATE = 16000;
const orb = document.getElementById("orb");
// Recall's headless browser allows autoplay; resume() also covers any
// browser that starts the context suspended.
const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
audioCtx.resume();
let nextStartTime = 0;

function playChunk(arrayBuffer) {
  const sampleCount = Math.floor(arrayBuffer.byteLength / 2);
  if (sampleCount === 0) { return; }
  const int16 = new Int16Array(arrayBuffer, 0, sampleCount);
  const floats = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) { floats[i] = int16[i] / 32768; }
  // The buffer declares 16 kHz itself, so even if the context ended up at a
  // different hardware rate, WebAudio resamples on playback.
  const buffer = audioCtx.createBuffer(1, sampleCount, SAMPLE_RATE);
  buffer.getChannelData(0).set(floats);
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  // Gapless: schedule back-to-back on a rolling clock, never in the past.
  const now = audioCtx.currentTime;
  if (nextStartTime < now) { nextStartTime = now; }
  source.start(nextStartTime);
  nextStartTime += buffer.duration;
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(scheme + location.host + WS_PATH);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      let msg;
      try { msg = JSON.parse(event.data); } catch (err) { return; }
      if (msg && typeof msg.speaking === "boolean") {
        orb.classList.toggle("speaking", msg.speaking);
      }
      return;
    }
    playChunk(event.data);
  };
  ws.onclose = () => {
    orb.classList.remove("speaking");
    setTimeout(connect, 1000);
  };
}
connect();
</script>
</body>
</html>
"""


def _render_page(meeting_id: str) -> str:
    ws_path = f"/output-media/{meeting_id}/ws"
    # JSON-encode for safe embedding in the <script>; escape "<" so a hostile
    # meeting id can never smuggle a </script> into the page.
    ws_path_js = json.dumps(ws_path).replace("<", "\\u003c")
    return _PAGE_TEMPLATE.replace("__WS_PATH__", ws_path_js)


async def _await_disconnect(websocket: WebSocket) -> None:
    """Return when the page goes away — that is this task's only job."""
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
    except Exception:
        return


def build_output_media_router() -> APIRouter:
    """The Output Media surface: the orb page + its per-meeting audio feed."""
    media_router = APIRouter()

    @media_router.get("/output-media/{meeting_id}", response_class=HTMLResponse)
    async def output_media_page(meeting_id: str) -> HTMLResponse:
        return HTMLResponse(_render_page(meeting_id))

    @media_router.websocket("/output-media/{meeting_id}/ws")
    async def output_media_feed(websocket: WebSocket, meeting_id: str) -> None:
        # Never-throw boundary: a page disconnect or send failure is normal
        # churn — clean exit, the channel stays for a reconnect.
        channel = channel_for(meeting_id)
        try:
            await websocket.accept()
        except Exception:
            return
        token = channel._attach()
        pump = asyncio.ensure_future(channel._pump(token, websocket))
        watch = asyncio.ensure_future(_await_disconnect(websocket))
        try:
            await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            pump.cancel()
            watch.cancel()
            channel._detach(token)  # before any await: detach must always land
            try:
                await asyncio.gather(pump, watch, return_exceptions=True)
            except BaseException:
                # The test-client cancel scope may re-cancel us here; the
                # detach already happened and the exit stays clean.
                pass

    return media_router


#: The importable router the control-plane server mounts via
#: ``app.include_router(output_media.router)``.
router: Final[APIRouter] = build_output_media_router()
