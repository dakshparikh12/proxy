"""BUG 4 (Law 3) — 'mute yourself' must actually silence the bot.

The conversational audio rides the Output-Media WEBPAGE channel (``speak`` → ``OutputMediaChannel.
write_audio``). Muting must therefore suppress that channel's ``write_audio`` (no PCM plays while
muted) AND drop any in-flight buffered PCM so audio stops NOW; unmute restores it. These prove the
channel's mute primitive and that the wired ``MeetingConnection`` 'mute'/'unmute' medium flips it.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def test_mute_suppresses_write_audio_and_drops_buffered_pcm() -> None:
    """A muted channel enqueues no PCM, and any already-buffered PCM is dropped; state frames
    (speaking/screen) survive so the page stays in sync. Unmute restores audio."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-mute-1")

        # buffer some PCM + a state frame BEFORE muting
        await ch.write_audio(b"\x01\x02\x03\x04")
        await ch.set_speaking(True)
        pcm_before = [f for f in ch._frames if isinstance(f, bytes)]
        assert pcm_before == [b"\x01\x02\x03\x04"]

        # mute: in-flight PCM is dropped, the state frame is kept
        ch.mute()
        assert ch.muted() is True
        assert [f for f in ch._frames if isinstance(f, bytes)] == []  # PCM dropped
        assert any(isinstance(f, str) and json.loads(f).get("speaking") is True for f in ch._frames)

        # while muted, write_audio is a no-op (nothing rides into the room)
        await ch.write_audio(b"\x09\x09")
        assert [f for f in ch._frames if isinstance(f, bytes)] == []

        # unmute: audio flows again
        ch.unmute()
        assert ch.muted() is False
        await ch.write_audio(b"\x0a\x0b")
        assert [f for f in ch._frames if isinstance(f, bytes)] == [b"\x0a\x0b"]

    asyncio.run(_run())


def test_meeting_connection_mute_medium_silences_the_channel_then_unmute_restores() -> None:
    """End-to-end through the driver: a 'mute' medium flips the wired audio-mute sink so the channel
    suppresses PCM; 'unmute' lifts it. Mirrors the provisioner wiring (channel.mute/unmute)."""
    from in_meeting.meeting_connection import MeetingConnection
    from in_meeting.output_media import OutputMediaChannel

    class _Speak:
        async def say(self, text: str) -> None: ...
        async def cut(self) -> None: ...

    class _Room:
        def __init__(self) -> None:
            self.muted = False

        async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...
        async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...

        async def mute(self, bot_id: str) -> None:
            self.muted = True

        async def unmute(self, bot_id: str) -> None:
            self.muted = False

    async def _run() -> None:
        ch = OutputMediaChannel("m-conn-1")

        async def _audio_mute(muted: bool) -> None:
            ch.mute() if muted else ch.unmute()

        conn = MeetingConnection(
            speak=_Speak(), room=_Room(), bot_id="bot-1", audio_mute=_audio_mute
        )

        send = await conn.to_meeting("", medium="mute")
        assert send.ok and send.medium == "mute"
        assert ch.muted() is True
        # a spoken chunk after mute does not play:
        await ch.write_audio(b"\x01\x02")
        assert [f for f in ch._frames if isinstance(f, bytes)] == []

        send = await conn.to_meeting("", medium="unmute")
        assert send.ok and send.medium == "unmute"
        assert ch.muted() is False
        await ch.write_audio(b"\x03\x04")
        assert [f for f in ch._frames if isinstance(f, bytes)] == [b"\x03\x04"]

    asyncio.run(_run())


def test_recall_transport_mute_makes_no_wire_call() -> None:
    """BUG 4: ``RecallTransport.mute`` no longer issues the nonexistent ``DELETE .../output_audio/``
    (a 404 for a real meeting) — it only flips the in-process clip-suppression flag. The conversational
    silence lives on the webpage channel, not a wire call here."""
    from transport.recall import RecallTransport

    calls: list[Any] = []

    async def _spy_call(thunk: Any, **_: Any) -> Any:
        calls.append(thunk)
        return await thunk()

    async def _run() -> None:
        t = RecallTransport(_spy_call, api_key="k")
        await t.mute("bot-1")
        assert calls == []  # no round-trip fired
        # the clip-path flag is set (so an output_audio POST would still be suppressed):
        assert t.output_media("bot-1") is not None

    asyncio.run(_run())
