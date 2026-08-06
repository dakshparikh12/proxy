"""OUTPUT-MEDIA — the Recall Output Media webpage, the per-meeting audio
channel, and the orb.

Recall bots join with ``output_media.camera = {kind: "webpage", config: {url}}``
(``RECALL_OUTPUT_MEDIA_URL`` on the join path): Recall loads that URL in a
headless browser and streams the PAGE — its canvas and its audio — into the
meeting as the bot's camera + microphone. This module is that missing surface:

* ``OutputMediaChannel`` — the server-side handle the speak path writes into:
  ``write_audio(pcm)`` enqueues raw PCM (s16le, 44.1 kHz, mono — the format the
  speech synth emits) and ``set_speaking(bool)`` drives the orb pulse.
* ``build_output_media_router()`` / ``router`` — the FastAPI router serving
  ``GET /output-media/{meeting_id}`` (the orb page, one inline HTML string —
  no build step, no external assets) and ``WS /output-media/{meeting_id}/ws``
  (host→page: PCM as BINARY frames, ``{"speaking": bool}`` as JSON text; and
  page→host: periodic ``{"type":"stats", ...}`` telemetry frames the host logs —
  ground truth for ctx rate / worklet-load / player path / underruns from inside
  Recall's headless browser).
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

Backpressure contract: the outbound PCM buffer is BOUNDED (``MAX_BUFFERED_FRAMES``)
but speech is NEVER silently dropped. When a page is attached and draining, a full
buffer makes ``write_audio`` AWAIT until the pump frees a slot — real backpressure,
so the speak path (whose ``_write`` is async) throttles to the wire's drain rate
instead of bursting a whole sentence in and overflowing (the live-run choppiness:
a whole sentence's PCM was dumped in one un-awaited loop, the deque overflowed
drop-oldest, and mid-sentence audio the page never saw was silently discarded).
Only when NO page is attached to make progress does the buffer fall back to a
bounded drop-oldest — with an HONEST overflow log (never a silent loss), and only
because a page that never comes cannot be waited on forever without stalling the
whole meeting loop. A page that attaches late receives the retained tail. A page
disconnect keeps the channel (the page may reconnect); ``close_channel`` is the
deliberate end-of-meeting teardown. State frames (speaking/screen/cut) NEVER block
— they are small ordered control, and a cut must reach the page even mid-backpressure.

Dependency note: ``fastapi``/``starlette`` are resolved from the workspace venv
(already a member dependency elsewhere); declaring them in this package's own
metadata is a tracked, consolidated cleanup.
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Final

from fastapi import APIRouter, WebSocket
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_BUFFERED_FRAMES",
    "MAX_SCREEN_HTML_BYTES",
    "OutputMediaChannel",
    "ScreenResult",
    "build_output_media_router",
    "channel_for",
    "close_channel",
    "router",
]

#: Backpressure threshold on buffered outbound PCM frames per meeting. At 120 ms of
#: 44.1 kHz s16le per chunk (``tts_chunk_ms``), 256 frames is ~30 s of audio. This is
#: NO LONGER a silent drop line: while a page drains, ``write_audio`` AWAITS here rather
#: than discarding, so a whole-sentence burst throttles to the wire (the live choppiness
#: was drop-oldest overflow eating mid-sentence PCM). It is generous headroom so a normal
#: sentence never blocks; a sustained overrun blocks the (async) producer, which is correct.
MAX_BUFFERED_FRAMES: Final[int] = 256

#: The PCM sample rate the page's audio pipeline rides (s16le, mono). The synth + the wire
#: + the page are all 44.1 kHz (``transport.tts.SAMPLE_RATE_HZ``); this mirrors that so the
#: buffer-duration reasoning above is sound (the prior 16 kHz here was stale — the page JS
#: hard-codes 44100 and the framing is 120 ms of 44.1 kHz).
SAMPLE_RATE_HZ: Final[int] = 44_100

#: Cap on a single ``screen_html`` content frame. The agent shows self-contained artifacts
#: (a rendered doc/mockup/diff) — 256 KiB is a generous page while bounding the wire + the
#: page's srcdoc against a runaway paste. Beyond it, ``show_screen`` returns an honest error
#: rather than truncating (a silent half-page would violate Law 2).
MAX_SCREEN_HTML_BYTES: Final[int] = 256 * 1024


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """The honest outcome of a ``show_screen`` call — what ACTUALLY happened (Law 2).

    ``rendered`` is True only when a render frame was enqueued to the channel; ``kind`` is
    ``"url"`` | ``"content"`` | ``"clear"`` on success, ``"error"`` on refusal. ``detail`` is a
    human-readable line the sink/connection surface verbatim (it also states when no page is
    currently connected — the frame still delivers on reconnect)."""

    rendered: bool
    kind: str
    detail: str


def _looks_like_url(value: str) -> bool:
    """A bare http(s) URL (a surface to point an iframe at) vs. content to render via srcdoc."""
    v = value.lstrip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _wrap_content_html(content: str) -> str:
    """Wrap agent-produced content as a self-contained, always-renders HTML document.

    If it already looks like a full HTML document, ride it as-is; otherwise wrap bare text /
    markdown-ish content in a tiny readable shell (dark bg, padded, monospace-friendly,
    whitespace preserved) so a plan/diff/answer is presentation-ready without any build step."""
    lowered = content.lstrip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        return content
    body: str
    if "<" in content and ">" in content:
        # Content already carries HTML tags (e.g. "<h1>..</h1>") — render it as markup.
        body = content
    else:
        # Bare text/markdown: escape and preserve whitespace so it reads as written.
        body = f"<pre>{_html.escape(content)}</pre>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>"
        "html,body{margin:0;background:#0b0f14;color:#e6edf3;"
        "font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;}"
        "body{padding:4vmin 5vmin;box-sizing:border-box;}"
        "pre{white-space:pre-wrap;word-wrap:break-word;margin:0;}"
        "h1,h2,h3{color:#9ecbff;} a{color:#6cb6ff;} "
        "table{border-collapse:collapse;} td,th{border:1px solid #30363d;padding:.3em .6em;}"
        "</style></head><body>" + body + "</body></html>"
    )


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
        #: The outbound buffer is UNBOUNDED at the deque level; the bound is enforced by
        #: BACKPRESSURE in ``write_audio`` (await while a draining page has ``_maxsize`` PCM
        #: frames queued) — so speech is never silently dropped. A plain ``deque`` (no maxlen)
        #: guarantees no drop-oldest can eat a frame while the producer is throttled.
        self._frames: deque[bytes | str] = deque()
        self._maxsize = max(1, maxsize)
        #: Count of PCM (bytes) frames currently buffered — the backpressure quantity (state
        #: ``str`` frames are tiny control and never counted / never blocked).
        self._pcm_buffered = 0
        #: Set whenever the buffered PCM drops BELOW ``_maxsize`` (the pump drained, or a
        #: cut/mute/close cleared it) — the signal a blocked ``write_audio`` waits on. Starts
        #: set (empty buffer = space available). Lives on the consumer loop.
        self._space: asyncio.Event | None = None
        self._attachment: object | None = None
        self._consumer_loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._closed = False
        #: While muted (C5), every PCM enqueue is suppressed and any in-flight buffered PCM is
        #: dropped — the conversational audio that rides THIS channel is silenced (Law 3, human
        #: control is absolute). State-only frames (speaking/screen) still ride so the page stays
        #: in sync. ``unmute`` lifts it; a human mute wins regardless of the wire.
        self._muted = False
        #: The URL Proxy last chose to SHOW on its Output-Media surface (the agent's ``screen``
        #: medium). Recorded honestly (the current shown surface) — never a fabricated success.
        #: The orb page reads it as a state message and swaps its view to the URL; absent = the orb.
        self._screen_url: str = ""

    # -- the speak-path surface ---------------------------------------------

    async def write_audio(self, pcm: bytes) -> None:
        """Enqueue one raw PCM chunk (s16le, 44.1 kHz, mono) for the page, with BACKPRESSURE.

        While muted (C5) the enqueue is DROPPED — no PCM plays into the room until unmute
        lifts the flag (Law 3, human control is absolute).

        Backpressure (the choppiness fix): if a page is attached and draining but the buffer
        already holds ``_maxsize`` PCM frames, AWAIT until the pump frees a slot rather than
        appending (which, drop-oldest, silently ate mid-sentence audio in the live run). The
        speak path's ``_write`` is async, so this simply throttles the whole-sentence burst to
        the wire's drain rate — speech is never lost. A cut/mute/close releases the wait at once
        (it clears the PCM and sets ``_space``), so a barge-in never deadlocks behind a full
        buffer. When NO page is draining (nothing can free a slot), we do NOT block the meeting
        loop forever — we append and, past the cap, drop the oldest PCM with an HONEST log (a
        page that never connects cannot be waited on; the loss is recorded, never silent)."""
        if self._muted:
            return
        # Backpressure only when a page is attached AND its wait-event lives on THIS loop (the
        # producer and the pump share the loop in the control-plane). Otherwise fall through to
        # the bounded-with-honest-log path (no consumer to make progress → never block forever).
        if self._space is not None and self._attachment is not None and self._on_consumer_loop():
            while (
                self._pcm_buffered >= self._maxsize
                and not self._muted
                and not self._closed
                and self._attachment is not None
                and self._space is not None
            ):
                space = self._space  # re-read each iter: a re-attach swaps the event
                space.clear()
                await space.wait()
            if self._muted or self._closed:
                return  # mute/close won the race while we waited — drop this chunk honestly
        elif self._pcm_buffered >= self._maxsize:
            # No draining page to free a slot: bound the buffer so a dead page can't grow memory
            # unboundedly, but NEVER silently — drop the oldest PCM and log it honestly (Law 2).
            self._drop_oldest_pcm()
            logger.warning(
                "output-media buffer full with no draining page (meeting=%s) — dropped oldest "
                "PCM frame (%d buffered); audio will have a gap until a page attaches",
                self.meeting_id, self._pcm_buffered,
            )
        self._frames.append(pcm)
        self._pcm_buffered += 1
        self._notify()

    def _on_consumer_loop(self) -> bool:
        """True iff the currently-running loop is the attached page's consumer loop — the only
        loop on which awaiting ``_space`` makes progress (the pump sets it there)."""
        try:
            return asyncio.get_running_loop() is self._consumer_loop
        except RuntimeError:
            return False

    def _drop_oldest_pcm(self) -> None:
        """Drop the single oldest buffered PCM (bytes) frame, keeping ordered state frames. Used
        ONLY on the no-draining-page fallback (logged honestly by the caller)."""
        for i, f in enumerate(self._frames):
            if isinstance(f, bytes):
                del self._frames[i]
                self._pcm_buffered = max(0, self._pcm_buffered - 1)
                return

    def mute(self) -> None:
        """Silence the conversational audio on this channel (C5): suppress every further
        ``write_audio`` enqueue AND drop any in-flight buffered PCM so audio stops NOW. State
        frames (speaking/screen) are kept so the page stays in sync. Idempotent."""
        self._muted = True
        # Drop only the buffered PCM (bytes); keep ordered state messages (str) intact.
        self._frames = deque(f for f in self._frames if not isinstance(f, bytes))
        self._pcm_buffered = 0
        self._release_space()  # a writer blocked on backpressure must unblock (then drop, muted)

    def unmute(self) -> None:
        """Lift the mute (C5): later ``write_audio`` enqueues ride again. Idempotent."""
        self._muted = False

    def muted(self) -> bool:
        """Is this channel's conversational audio currently muted?"""
        return self._muted

    async def set_speaking(self, speaking: bool) -> None:
        """Signal the orb pulse: True while Proxy speaks, False after."""
        self._frames.append(json.dumps({"speaking": speaking}))
        self._notify()

    async def cut(self) -> None:
        """Barge-in propagation (Law 3): a human talked over Proxy — stop the room's audio NOW.

        The host-side ``SpeakPipe.cut`` already drops buffered text / queued sentences / the in-flight
        synth, but the PAGE has ALREADY scheduled seconds of WebAudio (``source.start(nextStartTime)``),
        so without this it keeps playing over the human. This drops any PCM still buffered on the wire
        AND enqueues a ``{"type":"cut"}`` control frame the page acts on immediately: stop every
        scheduled source and reset its playback cursor. State frames stay ordered around it (the cut
        rides right where it was issued). Idempotent and never-throw."""
        # Drop buffered PCM (bytes) that hasn't reached the page yet; keep ordered state (str) frames.
        self._frames = deque(f for f in self._frames if not isinstance(f, bytes))
        self._pcm_buffered = 0
        self._release_space()  # a writer blocked on backpressure must unblock so the cut lands
        self._frames.append(json.dumps({"type": "cut"}))
        self._notify()

    async def show_screen(self, value: str) -> ScreenResult:
        """Show ``value`` on the Output-Media surface (the agent's ``screen`` medium), honestly.

        ``value`` is either a URL (http/https — pointed at via a sandboxed iframe) or CONTENT
        (raw HTML / text / markdown — wrapped into a self-contained document and rendered via
        iframe ``srcdoc``, which always renders: no X-Frame-Options / CSP risk). Empty ⇒ back to
        the orb. Content is preferred because external sites often refuse to be embedded.

        Returns a :class:`ScreenResult` describing what ACTUALLY happened — a render frame is
        enqueued only on success, and the detail states when no page is currently attached (the
        frame still delivers on reconnect). This is never a fabricated success (Law 2).
        """
        clean = (value or "").strip()
        page_note = "" if self.connected() else " (no page connected yet — delivers on reconnect)"

        if not clean:
            self._screen_url = ""
            self._frames.append(json.dumps({"screen": ""}))
            self._notify()
            return ScreenResult(True, "clear", f"cleared the screen — back to the orb{page_note}")

        if _looks_like_url(clean):
            self._screen_url = clean
            self._frames.append(json.dumps({"screen": clean}))
            self._notify()
            return ScreenResult(True, "url", f"showing {clean}{page_note}")

        # CONTENT (the preferred, always-renders path): wrap and ride srcdoc.
        doc = _wrap_content_html(clean)
        size = len(doc.encode("utf-8"))
        if size > MAX_SCREEN_HTML_BYTES:
            return ScreenResult(
                False,
                "error",
                f"content too large to show ({size} bytes exceeds the "
                f"{MAX_SCREEN_HTML_BYTES}-byte screen limit) — nothing was shown",
            )
        self._screen_url = ""  # content mode is not a URL surface
        self._frames.append(json.dumps({"screen_html": doc}))
        self._notify()
        preview = clean[:60].replace("\n", " ")
        return ScreenResult(True, "content", f"showing content ({size} bytes): {preview!r}{page_note}")

    def screen_url(self) -> str:
        """The URL currently shown on this meeting's Output-Media surface ("" ⇒ the orb, or
        content mode which is not a URL surface)."""
        return self._screen_url

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

    def _release_space(self) -> None:
        """Signal a ``write_audio`` blocked on backpressure that a slot is free (the pump drained,
        or a cut/mute/close cleared the PCM). Set on the consumer loop when we can (cross-loop-safe
        via ``call_soon_threadsafe``); a direct set when already on that loop or none is known."""
        space = self._space
        if space is None:
            return
        loop = self._consumer_loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            space.set()
            return
        try:
            loop.call_soon_threadsafe(space.set)
        except RuntimeError:
            space.set()

    def _attach(self) -> object:
        """Attach the calling WS handler as THE page; latest attach wins."""
        superseded = self._wake
        token: object = object()
        self._attachment = token
        self._consumer_loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        wake.set()  # deliver anything already buffered immediately
        self._wake = wake
        # The backpressure event lives on THIS (consumer) loop; start it set (a writer only waits
        # once the buffer is genuinely full, and the pump keeps it set while there is headroom).
        space = asyncio.Event()
        if self._pcm_buffered < self._maxsize:
            space.set()
        self._space = space
        if superseded is not None:
            superseded.set()  # release a stale pump so it can notice and exit
        return token

    def _detach(self, token: object) -> None:
        """Detach, but only if `token` is still the current attachment."""
        if self._attachment is token:
            self._attachment = None
            # Release any backpressured writer BEFORE tearing down the consumer refs: with no page
            # draining, a blocked writer must fall through to the bounded-with-log path, not hang.
            self._release_space()
            self._consumer_loop = None
            self._wake = None
            self._space = None

    def _close(self) -> None:
        self._closed = True
        self._frames.clear()
        self._pcm_buffered = 0
        wake = self._wake
        if wake is not None:
            wake.set()
        self._release_space()  # unblock a writer awaiting backpressure so it exits (closed → drop)

    async def _pump(self, token: object, websocket: WebSocket) -> None:
        """Drain frames to the attached page until superseded, closed, or dead."""
        while self._attachment is token and not self._closed:
            while self._frames and self._attachment is token and not self._closed:
                frame = self._frames.popleft()
                is_pcm = isinstance(frame, bytes)
                if is_pcm:
                    # A PCM slot just freed — decrement and release any backpressured writer BEFORE
                    # the (awaiting) send, so the producer refills the wire while this frame ships.
                    self._pcm_buffered = max(0, self._pcm_buffered - 1)
                    if self._pcm_buffered < self._maxsize:
                        self._release_space()
                try:
                    if isinstance(frame, bytes):
                        await websocket.send_bytes(frame)
                    else:
                        await websocket.send_text(frame)
                except BaseException:
                    # Send failed (or the handler was cancelled) mid-frame:
                    # put the frame back so a reconnecting page still gets it.
                    self._frames.appendleft(frame)
                    if is_pcm:
                        self._pcm_buffered += 1
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
  /* The screen surface: a full-viewport iframe that replaces the orb while active. */
  #screen {
    position: fixed; inset: 0; width: 100%; height: 100%;
    border: 0; background: #0b0f14; display: none;
  }
  #screen.active { display: block; }
  #stage.hidden { display: none; }
</style>
</head>
<body>
<div id="stage"><div id="orb"></div></div>
<iframe id="screen" sandbox="allow-scripts allow-same-origin"></iframe>
<script>
"use strict";
const WS_PATH = __WS_PATH__;
const SAMPLE_RATE = 44100;
const orb = document.getElementById("orb");
const stage = document.getElementById("stage");
const screen = document.getElementById("screen");

// The screen surface (the agent's 'screen' medium). A URL points the iframe at a page; raw
// html rides srcdoc (self-contained, always renders — no X-Frame-Options blank). An empty
// value (or null) returns to the orb. The orb code is untouched — screen just replaces it
// visually while active.
function showScreenUrl(url) {
  if (!url) { clearScreen(); return; }
  screen.removeAttribute("srcdoc");
  screen.src = url;
  screen.classList.add("active");
  stage.classList.add("hidden");
}
function showScreenHtml(html) {
  if (!html) { clearScreen(); return; }
  screen.removeAttribute("src");
  screen.srcdoc = html;
  screen.classList.add("active");
  stage.classList.add("hidden");
}
function clearScreen() {
  screen.classList.remove("active");
  screen.removeAttribute("src");
  screen.removeAttribute("srcdoc");
  stage.classList.remove("hidden");
}
// CONTINUOUS-STREAM PLAYER (FIX 4 — the best audio). The old player scheduled each
// ~120ms PCM chunk as its OWN AudioBufferSourceNode created at 44100 on a context
// running at the browser's native rate — so WebAudio RESAMPLED EVERY CHUNK SEPARATELY,
// planting an interpolation seam at each chunk boundary (~8 seams/sec = the classic
// choppy/gravelly voice). A rolling cursor can't fix a per-chunk resample seam. So we
// play ONE continuous stream instead: an AudioWorklet pulls from a single Float32 FIFO
// that every incoming chunk is APPENDED to — no per-chunk buffers, no per-chunk resample,
// no seams. On underrun the worklet emits ramped silence (a ~5ms fade so even a true gap
// is click-free) and re-prebuffers before resuming. Sample-rate is handled ONCE: we ask
// for a 44100 context (Chrome honors it → zero resampling in our path) and, only if the
// created context reports a different rate, resample ONCE at append (never per chunk).
const PREBUFFER_S = 0.35;   // samples to bank before emitting after silence (jitter cushion)
const RAMP_S = 0.005;       // fade-out/in at underrun gap edges → click-free silence

// The worklet processor SOURCE — an inline module (the page is one served HTML string, so
// we register it from a Blob URL). It owns the FIFO and the prebuffer/underrun/cut logic;
// the main thread only decodes+appends and sends control messages over the port.
const WORKLET_SRC = `
class StreamProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opt = (options && options.processorOptions) || {};
    this._fifo = new Float32Array(0);   // the single continuous sample FIFO
    this._read = 0;                     // read cursor into _fifo
    this._prebuffer = Math.max(1, Math.floor((opt.prebufferS || 0.35) * sampleRate));
    this._ramp = Math.max(1, Math.floor((opt.rampS || 0.005) * sampleRate));
    this._priming = true;               // banking samples before the first emit
    this._fadingIn = 0;                 // remaining fade-in samples after an underrun
    // TELEMETRY counters — the worklet is the ONLY place that sees the real FIFO/underrun/cut
    // truth inside Recall's headless Chrome. It reports them up on a 'stats?' request (the main
    // thread polls every ~5s and ships the reply back over the WS). counted since load.
    this._samplesAppended = 0;          // total samples ever fed into the FIFO
    this._underruns = 0;                // times process() hit a dry FIFO mid-stream
    this._cuts = 0;                     // barge-in clears
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m && m.type === 'samples') { this._append(m.samples); return; }
      if (m && m.type === 'cut') { this._cut(); return; }
      if (m && m.type === 'stats?') {
        // Snapshot the counters + the live FIFO depth (samples currently banked ahead of play).
        this.port.postMessage({
          type: 'stats', samplesAppended: this._samplesAppended,
          underruns: this._underruns, cuts: this._cuts, fifoDepth: this._available(),
        });
        return;
      }
    };
  }
  _available() { return this._fifo.length - this._read; }
  _append(samples) {
    // Compact consumed head occasionally so _fifo can't grow unboundedly.
    if (this._read > 0 && this._read >= this._fifo.length) {
      this._fifo = new Float32Array(0); this._read = 0;
    }
    const keep = this._fifo.subarray(this._read);
    const next = new Float32Array(keep.length + samples.length);
    next.set(keep, 0); next.set(samples, keep.length);
    this._fifo = next; this._read = 0;
    this._samplesAppended += samples.length;
  }
  _cut() { this._fifo = new Float32Array(0); this._read = 0; this._priming = true; this._fadingIn = 0; this._cuts++; }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!out) { return true; }
    // Prebuffer: after silence, wait until we've banked PREBUFFER_S before emitting, so
    // jittered arrivals don't immediately underrun the first block.
    if (this._priming) {
      if (this._available() < this._prebuffer) { out.fill(0); return true; }
      this._priming = false; this._fadingIn = this._ramp;   // fade the first block in
    }
    for (let i = 0; i < out.length; i++) {
      if (this._available() <= 0) {
        // UNDERRUN: emit ramped silence (fade the tail out over _ramp), then re-prime so
        // the resume fades back in — no truncation, no click.
        out[i] = 0;
        this._priming = true; this._fadingIn = 0; this._underruns++;
        // fade the last emitted samples out:
        const start = Math.max(0, i - this._ramp);
        for (let j = start; j < i; j++) { out[j] *= (i - j) / (i - start || 1); }
        for (let k = i + 1; k < out.length; k++) { out[k] = 0; }
        return true;
      }
      let s = this._fifo[this._read++];
      if (this._fadingIn > 0) { s *= 1 - this._fadingIn / this._ramp; this._fadingIn--; }
      out[i] = s;
    }
    return true;
  }
}
registerProcessor('stream-processor', StreamProcessor);
`;

// Recall's headless browser allows autoplay; resume() also covers any browser that starts
// the context suspended. Ask for a 44100 context so our s16le@44.1k path needs ZERO
// resampling; if the browser refuses and reports another rate we resample ONCE at append.
//
// AUDIO-PROCESSING LEVERS (researched, none page-applicable): Recall CAPTURES this page's audio
// output — the getUserMedia/captureStream constraints (echoCancellation / noiseSuppression /
// autoGainControl) live on RECALL'S side and are undocumented + not page-controllable (docs.recall.ai
// "Output Media" / stream-media). Google Meet applies noise suppression + echo cancellation to ALL
// inbound participant audio automatically with NO sender bypass and NO "music mode" equivalent
// (support.google.com/meet/answer/9919960). So there is NO page-side lever to set here; the stats
// frame above is the instrument that will tell us at the next live session what the page actually got.
let audioCtx;
try {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
} catch (err) {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
}
audioCtx.resume();
const CTX_RATE = audioCtx.sampleRate;

let workletNode = null;
let gainNode = null;
let muted = false;

// TELEMETRY / PATH state — reported every ~5s so the next live session gives ground truth from
// INSIDE Recall's headless Chrome: which player path is live, whether the worklet actually loaded,
// how many frames the page received, and (from the worklet) the FIFO/underrun/cut truth.
let workletLoaded = false;      // true once addModule + node wiring succeeded
let activePath = "worklet";     // "worklet" (FIFO player) | "buffer" (scheduled-source fallback)
let framesReceived = 0;         // WS BINARY frames the page has received

async function startWorklet() {
  // The worklet module is registered from an inline Blob URL. CSP on blob: is a REAL risk for a
  // served page (a strict connect-src/script-src could reject the blob: worklet). addModule() then
  // REJECTS — we catch it below and fall back to the scheduled-buffer player rather than going
  // silent. gainNode is created FIRST so mute/connect work on the fallback path too.
  gainNode = audioCtx.createGain();
  gainNode.gain.value = muted ? 0 : 1;
  gainNode.connect(audioCtx.destination);
  const blob = new Blob([WORKLET_SRC], { type: "application/javascript" });
  const url = URL.createObjectURL(blob);
  await audioCtx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  workletNode = new AudioWorkletNode(audioCtx, "stream-processor", {
    outputChannelCount: [1],
    processorOptions: { prebufferS: PREBUFFER_S, rampS: RAMP_S },
  });
  workletNode.connect(gainNode);
  workletLoaded = true;
  activePath = "worklet";
}
// If the worklet fails to load (CSP on blob:, unsupported API, …) we do NOT go silent: switch to a
// FALLBACK scheduled-buffer player. It is the simple per-chunk-source path (a known seam risk under
// resampling) but AUDIBLE beats SILENT — and the stats frame reports activePath="buffer" so the next
// live session tells us definitively whether the worklet loaded at all.
let fallbackNextStart = 0;      // scheduling cursor for the fallback path
function startFallbackBuffer() {
  activePath = "buffer";
  workletLoaded = false;
  fallbackNextStart = 0;
}
function fallbackPlay(floats) {
  if (!gainNode || floats.length === 0) { return; }
  const buf = audioCtx.createBuffer(1, floats.length, CTX_RATE);
  buf.getChannelData(0).set(floats);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(gainNode);
  const now = audioCtx.currentTime;
  if (fallbackNextStart < now) { fallbackNextStart = now; }
  src.start(fallbackNextStart);
  fallbackNextStart += buf.duration;
}
const workletReady = startWorklet().catch((err) => { startFallbackBuffer(); });

// STREAMING resample (only used if CTX_RATE !== 44100 — e.g. Recall's headless Chrome refuses
// a 44.1k context and reports 48k). THE live-chop root cause was resampling each ~120ms chunk
// INDEPENDENTLY: phase restarted at 0 and the edge sample clamped at every chunk boundary — a
// discontinuity 8x/second that no transport fix could cure. This resampler is STATEFUL: it
// carries the fractional read position AND the previous chunk's tail sample across calls, so the
// output is mathematically identical to resampling the whole concatenated utterance at once.
// State resets on cut (a new utterance legitimately starts fresh).
const resampleState = { frac: 0, tail: 0, hasTail: false };
function resampleStream(floats, fromRate, toRate) {
  if (fromRate === toRate) { return floats; }
  const step = fromRate / toRate;  // input samples advanced per output sample
  const lead = resampleState.hasTail ? 1 : 0;
  const inp = new Float32Array(lead + floats.length);
  if (lead) { inp[0] = resampleState.tail; }
  inp.set(floats, lead);
  const out = [];
  let pos = resampleState.frac;  // fractional position into inp (bridges the chunk boundary)
  while (pos <= inp.length - 1) {
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, inp.length - 1);
    const frac = pos - i0;
    out.push(inp[i0] * (1 - frac) + inp[i1] * frac);
    pos += step;
  }
  resampleState.tail = inp[inp.length - 1];
  resampleState.hasTail = true;
  resampleState.frac = pos - (inp.length - 1);  // carry the remainder past the new tail
  return Float32Array.from(out);
}

function appendChunk(arrayBuffer) {
  const sampleCount = Math.floor(arrayBuffer.byteLength / 2);
  if (sampleCount === 0) { return; }
  const int16 = new Int16Array(arrayBuffer, 0, sampleCount);
  let floats = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) { floats[i] = int16[i] / 32768; }
  floats = resampleStream(floats, SAMPLE_RATE, CTX_RATE);
  if (floats.length === 0) { return; }
  workletReady.then(() => {
    if (workletNode) { workletNode.port.postMessage({ type: "samples", samples: floats }); }
    else { fallbackPlay(floats); }   // worklet never loaded → audible fallback beats silent
  });
}

function cutPlayback() {
  // Barge-in (Law 3): a human talked over Proxy. Clear the FIFO instantly so the interrupted
  // turn's remaining buffered samples never play on top of the human. The worklet re-primes.
  // The streaming-resampler state resets too: the next utterance starts a fresh phase.
  resampleState.frac = 0; resampleState.tail = 0; resampleState.hasTail = false;
  workletReady.then(() => {
    if (workletNode) { workletNode.port.postMessage({ type: "cut" }); }
    else { fallbackNextStart = 0; }   // fallback: drop the schedule so nothing queued plays on
  });
}

function setMuted(v) {
  muted = v;
  if (gainNode) { gainNode.gain.value = muted ? 0 : 1; }
}

// STATS REPORTER — every ~5s while the WS is open, ask the worklet for its FIFO/underrun/cut
// snapshot and ship one JSON {type:"stats", ...} text frame back over the SAME WS. The host logs
// each frame (meeting-tagged), giving ground truth from inside Recall's headless Chrome: is the ctx
// really 48k? did the worklet load? do underruns actually fire? Tiny, one-way page→host telemetry.
const STATS_INTERVAL_MS = 5000;
function sendStats(activeWs, workletStats) {
  if (!activeWs || activeWs.readyState !== 1) { return; }
  const s = workletStats || { samplesAppended: 0, underruns: 0, cuts: 0, fifoDepth: 0 };
  try {
    activeWs.send(JSON.stringify({
      type: "stats", ctxRate: CTX_RATE, requestedRate: SAMPLE_RATE,
      workletLoaded: workletLoaded, activePath: activePath, framesReceived: framesReceived,
      samplesAppended: s.samplesAppended, underruns: s.underruns, cuts: s.cuts, fifoDepth: s.fifoDepth,
    }));
  } catch (err) { /* WS closed under us — the next tick will no-op on readyState */ }
}
function startStatsReporter(activeWs) {
  const tick = () => {
    if (workletNode) {
      // Ask the worklet for its counters; it replies on the port and we ship the combined frame.
      const once = (e) => {
        if (e.data && e.data.type === "stats") {
          workletNode.port.removeEventListener("message", once);
          sendStats(activeWs, e.data);
        }
      };
      workletNode.port.addEventListener("message", once);
      workletNode.port.start && workletNode.port.start();
      workletNode.port.postMessage({ type: "stats?" });
    } else {
      // Fallback path has no worklet counters — still report the page-level truth (path, frames).
      sendStats(activeWs, null);
    }
  };
  return setInterval(tick, STATS_INTERVAL_MS);
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(scheme + location.host + WS_PATH);
  ws.binaryType = "arraybuffer";
  let statsTimer = null;
  ws.onopen = () => { statsTimer = startStatsReporter(ws); };
  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      let msg;
      try { msg = JSON.parse(event.data); } catch (err) { return; }
      if (msg && msg.type === "cut") {
        cutPlayback();
        orb.classList.remove("speaking");
        return;
      }
      if (msg && typeof msg.muted === "boolean") {
        setMuted(msg.muted);
      }
      if (msg && typeof msg.speaking === "boolean") {
        orb.classList.toggle("speaking", msg.speaking);
      }
      if (msg && "screen_html" in msg) {
        showScreenHtml(msg.screen_html);
      } else if (msg && "screen" in msg) {
        showScreenUrl(msg.screen);
      }
      return;
    }
    framesReceived++;
    appendChunk(event.data);
  };
  ws.onclose = () => {
    if (statsTimer !== null) { clearInterval(statsTimer); statsTimer = null; }
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


def _log_stats_frame(meeting_id: str, text: str) -> None:
    """Parse one inbound page→host ``{"type":"stats", ...}`` text frame and LOG it (meeting-tagged),
    one ``logger.info`` line. This is ground truth from inside Recall's headless Chrome — the ctx
    rate it actually got, whether the worklet loaded, which player path is live, and the worklet's
    FIFO/underrun/cut counters. A non-stats or malformed frame is ignored (never throws)."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return
    if not isinstance(msg, dict) or msg.get("type") != "stats":
        return
    logger.info(
        "output-media page stats (meeting=%s): ctxRate=%s requestedRate=%s workletLoaded=%s "
        "activePath=%s framesReceived=%s samplesAppended=%s underruns=%s cuts=%s fifoDepth=%s",
        meeting_id, msg.get("ctxRate"), msg.get("requestedRate"), msg.get("workletLoaded"),
        msg.get("activePath"), msg.get("framesReceived"), msg.get("samplesAppended"),
        msg.get("underruns"), msg.get("cuts"), msg.get("fifoDepth"),
    )


async def _await_disconnect(websocket: WebSocket, meeting_id: str = "") -> None:
    """Read the page→host WS until the page goes away. The channel's audio feed is one-way
    host→page, but the page also sends periodic ``stats`` text frames (telemetry from inside
    Recall's browser); each is parsed + logged here. Returns on disconnect."""
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                _log_stats_frame(meeting_id, text)
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
        watch = asyncio.ensure_future(_await_disconnect(websocket, meeting_id))
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
