"""The ``to_meeting`` contract is unified on Design B (T5).

Live design: the agent SPEAKS by writing its prose, which is streamed sentence-by-sentence to TTS;
``to_meeting`` is ONLY the non-spoken channels (chat/dm/screen/offer/mute/unmute). These tests pin
that one contract so the model is never handed two contradictory ways to talk (the two-contract
speaking bug): one canonical medium vocabulary, a consistent ``chat`` default, the stale Design-A
twin (``TO_MEETING_TOOL``) deleted, and no prime/prompt drift listing ``speak`` as a medium.
"""
from __future__ import annotations

import asyncio
import inspect


def test_advertised_media_is_the_one_canonical_non_spoken_vocabulary() -> None:
    from in_meeting.meeting_connection import ADVERTISED_MEDIA

    assert ADVERTISED_MEDIA == ("chat", "dm", "screen", "offer", "mute", "unmute", "raise_hand")
    # speaking is NOT a to_meeting medium under Design B — it is the streamed prose channel.
    for spoken in ("say", "speak", "voice"):
        assert spoken not in ADVERTISED_MEDIA


def test_dead_design_a_twin_is_deleted() -> None:
    import in_meeting.meeting_connection as mc

    assert not hasattr(mc, "TO_MEETING_TOOL"), "the stale Design-A tool schema must be gone"


def test_to_meeting_default_medium_is_chat() -> None:
    import in_meeting.meeting_connection as mc

    sig = inspect.signature(mc.MeetingConnection.to_meeting)
    assert sig.parameters["medium"].default == "chat"


def test_mcp_tool_advertises_exactly_the_canonical_media_and_never_say() -> None:
    """The in-sandbox MCP tool the agent actually sees advertises the canonical non-spoken mediums
    and NEVER offers say/speak/voice as a medium (the agent speaks by writing prose)."""
    import in_meeting.sandbox_meeting_mcp as mcp
    from in_meeting.meeting_connection import ADVERTISED_MEDIA

    src = inspect.getsource(mcp)
    for medium in ADVERTISED_MEDIA:
        assert f"'{medium}'" in src, f"the MCP tool must advertise '{medium}'"
    for spoken in ("'say'", "'speak'", "'voice'"):
        assert spoken not in src, f"the MCP tool must not advertise {spoken} as a medium"
    # the default medium is chat (Design B) and there is no cross-ref to the deleted twin
    assert 'medium: str = "chat"' in src
    assert "TO_MEETING_TOOL" not in src


def test_route_keeps_say_as_the_voice_channel_and_unknown_falls_back_but_default_is_chat() -> None:
    """say/speak/voice still route to the voice sink (the streamed prose rides medium='say' over the
    SAME relay), an unknown medium still falls back to voice (the documented safety net), but an
    absent medium now defaults to the chat channel — not voice (Design B)."""
    from in_meeting.meeting_connection import MeetingConnection

    class _Speak:
        def __init__(self) -> None:
            self.said: list[str] = []

        async def say(self, t: str) -> None:
            self.said.append(t)

        async def cut(self) -> None: ...

    class _Room:
        def __init__(self) -> None:
            self.chats: list[str] = []

        async def post_chat(self, b: str, m: str, *, pinned: bool = False) -> None:
            self.chats.append(m)

        async def send_dm(self, *a: object, **k: object) -> None: ...
        async def mute(self, b: str) -> None: ...
        async def unmute(self, b: str) -> None: ...

    async def _run() -> None:
        sp, rm = _Speak(), _Room()
        conn = MeetingConnection(speak=sp, room=rm, bot_id="b")
        # the voice channel the prose stream rides is still handled
        assert (await conn.to_meeting("out loud", medium="say")).medium == "say"
        assert sp.said[-1] == "out loud"
        # unknown medium → the safety-net voice fallback (never drop the words)
        r = await conn.to_meeting("carry me", medium="telepathy")
        assert r.medium == "say" and sp.said[-1] == "carry me"
        # absent/empty medium → the chat DEFAULT (Design B), NOT voice
        r = await conn.to_meeting("posting")
        assert r.medium == "chat" and rm.chats[-1] == "posting"

    asyncio.run(_run())


def test_prime_and_wake_prompt_never_list_speak_as_a_to_meeting_medium() -> None:
    """Prime-drift fix: no comment/docstring/prompt lists ``speak`` as a to_meeting medium — the one
    consistent instruction is speaking = prose, to_meeting = the non-spoken channels."""
    import in_meeting.workroom as wm

    src = inspect.getsource(wm)
    assert "speak/chat/dm/screen/offer/mute" not in src
