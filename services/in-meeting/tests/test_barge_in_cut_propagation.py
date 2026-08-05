"""ISSUE 2 (Law 3) — a barge-in must reach the PAGE, not just host-side state.

The founder tested live: the host-side ``SpeakPipe.cut`` landed, but the room kept hearing
Proxy for seconds after — the page had already SCHEDULED that audio into WebAudio, and nothing
told it to stop. These prove the full propagation path:

  partial → on_partial → connection.barge_in → SpeakPipe.cut → channel.cut → a ``{"type":"cut"}``
  control frame lands on the wire (the page reads it and stops every scheduled source).

Plus the behavioral end-to-end: the interrupted turn's remainder is dropped (cut latch), and the
INTERRUPTING utterance's final line is NOT suppressed by the cut path (it wakes normally).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def test_channel_cut_drops_buffered_pcm_and_sends_a_cut_control_frame() -> None:
    """The channel's ``cut`` primitive: any wire-buffered PCM is discarded and a single
    ``{"type":"cut"}`` control frame is enqueued for the page. Ordered state frames survive."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-cut-1")
        await ch.set_speaking(True)
        await ch.write_audio(b"\x01\x02\x03\x04")  # seconds of audio, not yet drained to a page
        await ch.write_audio(b"\x05\x06")
        assert [f for f in ch._frames if isinstance(f, bytes)]  # PCM is buffered

        await ch.cut()

        # PCM is gone; a cut control frame is present exactly once.
        assert [f for f in ch._frames if isinstance(f, bytes)] == []
        cut_frames = [
            f for f in ch._frames
            if isinstance(f, str) and json.loads(f).get("type") == "cut"
        ]
        assert len(cut_frames) == 1
        # The earlier speaking-state frame is preserved (page stays in sync).
        assert any(
            isinstance(f, str) and json.loads(f).get("speaking") is True for f in ch._frames
        )

    asyncio.run(_run())


def test_speakpipe_cut_propagates_a_cut_frame_to_the_real_channel() -> None:
    """The full host path: ``SpeakPipe.cut`` fires the wired channel's ``cut``, so the page gets a
    cut control frame — the room's already-scheduled audio is stopped, not just host-side buffers."""
    from in_meeting.output_media import OutputMediaChannel
    from in_meeting.speak import build_speak_sink

    async def _synth(text: str) -> Any:
        # A slow, chunked synth so a sentence is genuinely in flight when we cut.
        for _ in range(4):
            await asyncio.sleep(0.01)
            yield type("C", (), {"pcm": b"\x10\x11\x12\x13"})()

    async def _run() -> None:
        ch = OutputMediaChannel("m-cut-2")
        pipe = build_speak_sink(synthesize=_synth, channel=ch)
        await pipe.say("Here is a long sentence I am speaking. ")
        await asyncio.sleep(0.015)  # let some PCM stream into the channel
        assert pipe.speaking is True

        await pipe.cut()

        cut_frames = [
            f for f in ch._frames
            if isinstance(f, str) and json.loads(f).get("type") == "cut"
        ]
        assert len(cut_frames) == 1
        assert [f for f in ch._frames if isinstance(f, bytes)] == []  # no PCM survives the cut
        assert pipe.speaking is False

    asyncio.run(_run())


def test_page_js_handles_the_cut_frame_and_clears_the_stream() -> None:
    """The inline page JS must (a) handle a cut control frame and (b) clear the continuous-stream
    FIFO on it (barge-in, Law 3) so the interrupted turn's buffered audio never plays on top of the
    human. This asserts the exact JS shipped in the page so the client-side half can't regress.

    FIX 4 rebuild: the player is now a continuous-stream AudioWorklet over a single FIFO (no per-chunk
    source scheduling), so a cut CLEARS THE FIFO rather than stopping N scheduled sources. The full
    continuous-player assertions live in tests/test_output_media_stream_player.py."""
    from in_meeting.output_media import _render_page

    page = _render_page("m-page-1")
    # (a) a cut control frame is handled and clears playback:
    assert 'msg.type === "cut"' in page
    assert "cutPlayback()" in page
    # (b) the cut clears the FIFO instantly via the worklet port message (the new stream player):
    assert 'postMessage({ type: "cut" }' in page
    # the old per-chunk scheduling machinery is fully gone (no dead code, no per-chunk resample seam):
    assert "liveSources" not in page
    assert "createBufferSource" not in page


def test_barge_in_final_line_is_not_suppressed_by_the_cut_path() -> None:
    """Behavioral: after a barge-in cut, the INTERRUPTING utterance's final line still wakes Proxy
    normally (nothing in the cut path swallows it), while the interrupted turn's remainder stays
    dropped by the cut latch until the next wake's ``begin_turn``."""
    from in_meeting.meeting_connection import MeetingConnection

    class _Speak:
        def __init__(self) -> None:
            self.cuts = 0
            self._speaking = True

        @property
        def speaking(self) -> bool:
            return self._speaking

        async def say(self, text: str) -> None: ...

        async def cut(self) -> None:
            self.cuts += 1
            self._speaking = False

    class _Room:
        async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...
        async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...
        async def mute(self, bot_id: str) -> None: ...
        async def unmute(self, bot_id: str) -> None: ...

    async def _run() -> None:
        conn = MeetingConnection(speak=_Speak(), room=_Room(), bot_id="bot-1")

        # human barges in mid-turn: cut fires, latch up, the interrupted turn's rest is dropped
        await conn.barge_in()
        assert conn.cut_latched is True
        dropped = await conn.to_meeting("...the rest of the interrupted sentence", medium="say")
        assert dropped.ok is False and "barged-in" in dropped.detail

        # the NEXT wake begins (the interrupting line was addressed): latch lowers, speech flows
        conn.begin_turn()
        assert conn.cut_latched is False
        fresh = await conn.to_meeting("Responding to what you just said.", medium="say")
        assert fresh.ok is True

    asyncio.run(_run())
