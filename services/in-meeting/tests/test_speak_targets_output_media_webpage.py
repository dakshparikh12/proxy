"""BLOCKER C — the live say path pushes PCM into the OUTPUT-MEDIA webpage channel.

To be HEARD, synthesized Cartesia PCM must reach ``output_media.channel_for(meeting_id)`` — the
webpage the Recall bot loads as its camera/mic — NOT the rate-limited Recall ``output_audio`` clip
endpoint (``transport.recall._RecallOutputMedia.write_audio``). These prove the production speak
wiring (``in_meeting.speak.real_speak_sink`` → ``SpeakPipe`` → channel) targets the webpage channel:

* ``real_speak_sink(meeting_id)`` builds a pipe whose sink IS ``output_media.channel_for(meeting_id)``
  (an ``OutputMediaChannel``), so every ``write_audio`` lands on the webpage feed, never the clip API;
* a ``say`` → synth → the PCM bytes reach THAT channel's ``write_audio`` (buffered for the page),
  and the speaking-state pulse flips there too;
* the channel is NOT a Recall clip sink (``_RecallOutputMedia`` is a different type entirely).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from in_meeting import output_media
from in_meeting.speak import real_speak_sink


@dataclass
class _Chunk:
    pcm: bytes


class _FakeCartesia:
    """A stand-in for ``transport.tts.CartesiaTTS``: one deterministic PCM chunk per sentence."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def synthesize(self, text: str) -> AsyncIterator[_Chunk]:
        yield _Chunk(pcm=b"\x01\x02\x03\x04")  # even-length s16le, plays straight through


def _install_fake_tts(monkeypatch: Any) -> None:
    import transport.tts as tts_mod

    monkeypatch.setattr(tts_mod, "CartesiaTTS", _FakeCartesia)


def test_real_speak_sink_writes_into_output_media_webpage_channel(monkeypatch: Any) -> None:
    """The production pipe's sink IS this meeting's OutputMediaChannel — never the clip endpoint."""
    _install_fake_tts(monkeypatch)
    meeting_id = "m-blocker-c-1"

    pipe = real_speak_sink(meeting_id)
    webpage_channel = output_media.channel_for(meeting_id)

    # The pipe writes into the OUTPUT-MEDIA webpage channel, by identity — the exact surface the
    # Recall bot renders as its camera/mic feed.
    assert pipe._channel is webpage_channel
    assert isinstance(webpage_channel, output_media.OutputMediaChannel)

    # And it is NOT the Recall clip endpoint (a wholly different type / transport).
    from transport.recall import _RecallOutputMedia

    assert not isinstance(pipe._channel, _RecallOutputMedia)

    output_media.close_channel(meeting_id)


def test_say_delivers_pcm_to_the_webpage_channel_write_audio(monkeypatch: Any) -> None:
    """A say → synth → PCM reaches the webpage channel's write_audio (buffered for the page)."""
    _install_fake_tts(monkeypatch)
    meeting_id = "m-blocker-c-2"

    async def _run() -> None:
        pipe = real_speak_sink(meeting_id)
        channel = output_media.channel_for(meeting_id)
        await pipe.say("Hello team.")
        await pipe.flush()  # force the tail + drain the one-synth-at-a-time worker

        # The channel buffers frames destined for the webpage feed: the synthesized PCM bytes are
        # present as a BINARY frame (bytes), proving the audio reached the webpage sink.
        pcm_frames = [f for f in channel._frames if isinstance(f, bytes)]
        assert b"\x01\x02\x03\x04" in b"".join(pcm_frames)
        # The speaking pulse flipped True on this same channel (the orb lights on the webpage).
        assert any(isinstance(f, str) and '"speaking": true' in f for f in channel._frames)

        output_media.close_channel(meeting_id)

    asyncio.run(_run())


def test_output_media_routes_are_mounted_on_the_control_plane_app() -> None:
    """The /output-media/{id} page + /ws feed routes are mounted so the Recall bot can attach."""
    paths = {getattr(r, "path", "") for r in output_media.router.routes}
    assert "/output-media/{meeting_id}" in paths      # the orb webpage the bot loads
    assert "/output-media/{meeting_id}/ws" in paths    # the PCM/state feed the page consumes
