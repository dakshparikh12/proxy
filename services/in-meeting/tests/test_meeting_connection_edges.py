"""MeetingConnection — edge cases beyond the three already covered.

The existing suite (test_meeting_connection.py) proves each medium routes to the right vendor op,
that offer/screen degrade honestly when unwired, and that a vendor fault never crashes. These add
the finer edges: medium aliasing + case/whitespace normalization, an offer whose stage yields NO
approve URL (nothing posted to chat), the honest ``sent`` record on a failed send, and that a
default empty ``say`` is still a valid send. All with fakes — a simulated meeting.
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


def test_medium_aliases_and_case_whitespace_normalize() -> None:
    """The agent's medium string is normalized (strip + lowercase) and each family of aliases maps
    to the same physical op — 'SAY '/'speak'/'voice' all speak; 'message'/'post' chat; etc."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        speak, room = _FakeSpeak(), _FakeRoom()
        conn = MeetingConnection(speak=speak, room=room, bot_id="b")

        assert (await conn.to_meeting("a", medium="  SAY ")).medium == "say"
        assert (await conn.to_meeting("b", medium="Speak")).medium == "say"
        assert (await conn.to_meeting("c", medium="VOICE")).medium == "say"
        assert speak.said == ["a", "b", "c"]

        assert (await conn.to_meeting("hi", medium="Message")).medium == "chat"
        assert (await conn.to_meeting("yo", medium="POST")).medium == "chat"
        assert room.chats == ["hi", "yo"]

        assert (await conn.to_meeting("", medium="Silence")).medium == "mute" and room.muted
        assert (await conn.to_meeting("", medium="Resume")).medium == "unmute" and not room.muted

    asyncio.run(_run())


def test_dm_aliases_and_missing_recipient_records_honestly() -> None:
    """'direct'/'whisper' alias to dm; a dm with no recipient is an honest failed send (recorded
    on ``sent`` with the reason), never a silent drop and never a crash."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        room = _FakeRoom()
        conn = MeetingConnection(speak=_FakeSpeak(), room=room, bot_id="b")

        r = await conn.to_meeting("secret", medium="whisper", to="p-1")
        assert r.ok is True and r.medium == "dm" and "to=p-1" in r.detail
        assert room.dms == [("p-1", "secret")]

        r2 = await conn.to_meeting("secret", medium="direct")   # no recipient
        assert r2.ok is False and r2.medium == "dm" and "recipient" in r2.detail.lower()
        # both are recorded on the ordered audit trail:
        assert [s.medium for s in conn.sent] == ["dm", "dm"]
        assert [s.ok for s in conn.sent] == [True, False]

    asyncio.run(_run())


def test_offer_with_no_approve_url_posts_nothing_to_chat() -> None:
    """An offer whose stage returns an empty approve URL (approve path unavailable) still succeeds
    as an offer, but posts NO approve link to chat (nothing to click) — honest, not fabricated."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        room = _FakeRoom()

        async def _offer(content: str, _to: str) -> str:
            return ""   # staged, but no approve URL available

        conn = MeetingConnection(speak=_FakeSpeak(), room=room, bot_id="b", offer=_offer)
        r = await conn.to_meeting("apply this patch", medium="offer")
        assert r.ok is True and r.medium == "offer" and r.detail == ""
        assert room.chats == []   # no approve link posted (there was none)

    asyncio.run(_run())


def test_offer_posts_the_approve_link_when_staged() -> None:
    """When the stage yields an approve URL, it is surfaced into the room's chat so a human can
    click it (Law 3) — the ``sent`` record carries the URL as its detail."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        room = _FakeRoom()

        async def _offer(content: str, to: str) -> str:
            return "https://app/m/1/drafts/7/accept"

        conn = MeetingConnection(speak=_FakeSpeak(), room=room, bot_id="b", offer=_offer)
        r = await conn.to_meeting("open a PR", medium="offer", to="draft-ctx")
        assert r.ok is True
        assert r.detail == "https://app/m/1/drafts/7/accept"
        assert len(room.chats) == 1
        assert "approve:" in room.chats[0] and "/accept" in room.chats[0]

    asyncio.run(_run())


def test_failed_send_is_recorded_on_the_sent_trail_with_the_reason() -> None:
    """A vendor fault is caught into an honest MeetingSend(ok=False, detail=<reason>) AND appended
    to the ordered ``sent`` trail (so the session/audit sees it), never re-raised."""
    from in_meeting.meeting_connection import MeetingConnection

    class _BoomRoom(_FakeRoom):
        async def post_chat(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("recall 500")

    async def _run() -> None:
        conn = MeetingConnection(speak=_FakeSpeak(), room=_BoomRoom(), bot_id="b")
        r = await conn.to_meeting("post me", medium="chat")
        assert r.ok is False and "recall 500" in r.detail
        # the failed send is the last entry on the audit trail:
        assert conn.sent[-1] is r
        assert conn.sent[-1].ok is False

    asyncio.run(_run())


def test_empty_say_is_still_a_valid_send() -> None:
    """A bare ``to_meeting()`` (empty content, default medium) is a valid say — the driver never
    second-guesses the agent's intent; it just carries it."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        speak = _FakeSpeak()
        conn = MeetingConnection(speak=speak, room=_FakeRoom(), bot_id="b")
        r = await conn.to_meeting()
        assert r.ok is True and r.medium == "say"
        assert speak.said == [""]

    asyncio.run(_run())


def test_unknown_medium_falls_back_to_voice_and_notes_it() -> None:
    """An unknown medium never drops the agent's words — it falls back to voice and the ``sent``
    record NAMES the fallback in its detail (honest, not silent)."""
    from in_meeting.meeting_connection import MeetingConnection

    async def _run() -> None:
        speak = _FakeSpeak()
        conn = MeetingConnection(speak=speak, room=_FakeRoom(), bot_id="b")
        r = await conn.to_meeting("carry me anyway", medium="hologram")
        assert r.medium == "say" and r.ok is True
        assert "hologram" in r.detail and "said" in r.detail
        assert speak.said == ["carry me anyway"]

    asyncio.run(_run())
