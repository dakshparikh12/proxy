"""MeetingConnection — the host-side driver that carries Proxy's one dynamic intent to the room.

Proves each medium the agent can choose routes to the right physical vendor op, that the
world-touching `offer` stages a draft + surfaces an approve link (Law 3), that missing surfaces
degrade honestly, and that a vendor fault never crashes the meeting (§3.8). All with fakes — a
simulated meeting — so the routing is provable before any live vendor round-trip.
"""
from __future__ import annotations

import asyncio


class _FakeSpeak:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.cuts = 0

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def cut(self) -> None:
        self.cuts += 1


class _FakeRoom:
    def __init__(self) -> None:
        self.chats: list[str] = []
        self.dms: list[tuple[str, str]] = []
        self.muted = False

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.chats.append(message)

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.dms.append((participant_id, message))

    async def mute(self, bot_id: str) -> None:
        self.muted = True

    async def unmute(self, bot_id: str) -> None:
        self.muted = False


def test_every_medium_routes_and_degrades_and_never_crashes() -> None:
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        speak, room = _FakeSpeak(), _FakeRoom()
        offered: list[str] = []

        async def _offer(content: str, _to: str) -> str:
            offered.append(content)
            return "https://app/m/1/drafts/9/accept"

        async def _screen(url: str) -> str:
            return url

        conn = MeetingConnection(
            speak=speak, room=room, bot_id="bot-1", offer=_offer, screen=_screen
        )

        # say (default + explicit)
        assert (await conn.to_meeting("hello room")).medium == "say"
        assert (await conn.to_meeting("again", medium="voice")).ok
        assert speak.said == ["hello room", "again"]

        # chat
        r = await conn.to_meeting("posting this", medium="chat")
        assert r.ok and room.chats[-1] == "posting this"

        # dm needs a recipient; honest degrade without one
        assert (await conn.to_meeting("psst", medium="dm")).ok is False
        r = await conn.to_meeting("psst", medium="dm", to="p-42")
        assert r.ok and room.dms[-1] == ("p-42", "psst")

        # mute / unmute
        assert (await conn.to_meeting("", medium="mute")).ok and room.muted is True
        assert (await conn.to_meeting("", medium="unmute")).ok and room.muted is False

        # screen
        r = await conn.to_meeting("https://view/diff", medium="screen")
        assert r.ok and r.detail == "https://view/diff"

        # offer (world-touching) → stages a draft + posts the approve link (Law 3)
        r = await conn.to_meeting("open a PR with this fix", medium="offer")
        assert r.ok and offered == ["open a PR with this fix"]
        assert any("approve:" in c and "/accept" in c for c in room.chats)

        # unknown medium → falls back to voice, never drops the words
        r = await conn.to_meeting("mystery", medium="telepathy")
        assert r.medium == "say" and speak.said[-1] == "mystery"

        # the full ordered record is kept for audit/tests
        assert [s.medium for s in conn.sent][:3] == ["say", "say", "chat"]

    asyncio.run(_run())


def test_offer_and_screen_missing_degrade_honestly() -> None:
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        speak, room = _FakeSpeak(), _FakeRoom()
        conn = MeetingConnection(speak=speak, room=room, bot_id="b")  # no offer/screen wired
        assert (await conn.to_meeting("x", medium="offer")).ok is False
        assert (await conn.to_meeting("x", medium="screen")).ok is False
        # meeting still fine — say still works
        assert (await conn.to_meeting("still here", medium="say")).ok

    asyncio.run(_run())


def test_vendor_fault_never_crashes_the_meeting() -> None:
    from in_meeting.meeting_connection import MeetingConnection

    class _Boom(_FakeRoom):
        async def post_chat(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("recall 500")

    async def _run() -> None:
        conn = MeetingConnection(speak=_FakeSpeak(), room=_Boom(), bot_id="b")
        r = await conn.to_meeting("will fail", medium="chat")
        assert r.ok is False and "recall 500" in r.detail  # honest, not raised

    asyncio.run(_run())


def test_spoken_log_records_only_voice_and_stays_bounded() -> None:
    """The self-echo reference: every SPOKEN line (say/voice) is logged with a wall-clock stamp so
    the reactive loop can recognize Proxy's own acoustic echo (headphones-optional). Chat/DM are
    text — they never echo acoustically — so they are NOT logged. The log is bounded."""
    from in_meeting.meeting_connection import _SPOKEN_LOG_MAX, MeetingConnection

    async def _run() -> None:
        speak, room = _FakeSpeak(), _FakeRoom()
        conn = MeetingConnection(speak=speak, room=room, bot_id="b")

        await conn.to_meeting("the entry point is main() in core.py", medium="say")
        await conn.to_meeting("posting a link", medium="chat")   # text — not logged
        await conn.to_meeting("psst", medium="dm", to="p-1")      # text — not logged
        await conn.to_meeting("also this out loud", medium="voice")

        texts = [t for (_ts, t) in conn.spoken]
        assert texts == ["the entry point is main() in core.py", "also this out loud"]
        assert all(isinstance(ts, float) and ts > 0 for (ts, _t) in conn.spoken)

        # blank speech is not recorded; the log never grows past the bound
        await conn.to_meeting("   ", medium="say")
        for i in range(_SPOKEN_LOG_MAX + 20):
            await conn.to_meeting(f"line {i}", medium="say")
        assert len(conn.spoken) == _SPOKEN_LOG_MAX
        assert conn.spoken[-1][1] == f"line {_SPOKEN_LOG_MAX + 19}"  # newest kept

    asyncio.run(_run())
