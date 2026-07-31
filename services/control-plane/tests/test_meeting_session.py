"""MeetingSession — the reactive loop (transcript-in -> wake gate -> run -> respond).

The one place §0/§3 lives in code: a transcript line arrives -> it is appended to the
workroom's notes (continuous) -> the cheap word-bounded wake gate decides whether Proxy is
addressed -> on a wake the reactive turn runs in the workroom and the agent responds through
the one connection. These tests exercise the REAL MeetingSession with fakes at its two seams
(the workroom's run_ask/feed_transcript and the connection's to_meeting/sent record) — no live
vendor, no sandbox. The existing boot-path test proves the happy path end-to-end; here we prove
the loop's decision behaviour and its honest-degrade edges directly.

The response ALWAYS comes from the agent's OWN ``to_meeting`` choices, never our prose (Law 4):
either relayed live during the turn (recorded on the connection) or replayed afterward from
``result.sent`` (the recorded intents in the no-relay/file path). A clean turn with zero intents
is the agent choosing silence (cross-talk); an errored turn that delivered nothing gets ONE honest
degrade line. There is no ``result.text`` fallback.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any


def _result(*, text: str = "", error: str | None = None,
            sent: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    """A stand-in WorkroomResult carrying the fields _handle reads: text, error, and the agent's
    OWN recorded ``to_meeting`` intents (``sent``). ``sent`` defaults to [] (no recorded intents)."""
    return SimpleNamespace(text=text, error=error, sent=list(sent or []))


def _say(content: str) -> list[dict[str, Any]]:
    """One recorded 'say' intent — the common no-relay channel choice, so a scripted turn's result
    replays as a spoken line over the connection (the agent's OWN choice, not our prose)."""
    return [{"content": content, "medium": "say", "to": ""}]


class _FakeConnection:
    """Records what actually reached the room, mirroring MeetingConnection.sent / to_meeting."""

    def __init__(self) -> None:
        self.sent: list[SimpleNamespace] = []

    async def to_meeting(self, content: str, medium: str = "say", to: str | None = None) -> Any:
        rec = SimpleNamespace(medium=medium, ok=True, detail="", content=content)
        self.sent.append(rec)
        return rec


class _FakeWorkroom:
    """A workroom whose run_ask returns a scripted result and records the feed + asks it saw.

    ``on_run`` (optional) is invoked inside run_ask with the connection so a test can simulate
    the agent reaching the room live (a to_meeting relay call) during the turn."""

    def __init__(
        self,
        *,
        result: Any = None,
        connection: Any = None,
        on_run: Any = None,
        raise_on_run: bool = False,
    ) -> None:
        self.fed: list[str] = []
        self.asks: list[str] = []
        self._result = result if result is not None else _result(text="ok", sent=_say("ok"))
        self._connection = connection
        self._on_run = on_run
        self._raise_on_run = raise_on_run

    async def feed_transcript(self, md: str) -> None:
        self.fed.append(md)

    async def run_ask(self, ask: str) -> Any:
        self.asks.append(ask)
        if self._raise_on_run:
            raise RuntimeError("run_ask blew up")
        if self._on_run is not None:
            await self._on_run(self._connection)
        return self._result


def test_wake_gate_fires_on_voice_proxy_and_chat_atproxy_but_not_crosstalk() -> None:
    """The gate: a spoken 'proxy' wakes; a chat '@proxy' wakes; a bare word 'proxy' in chat,
    a common-noun 'proxy server', cross-talk, and self-speech do NOT wake (§0/§3)."""
    from control_plane.meeting_session import is_addressed

    # voice: naming proxy wakes (returns the verbatim ask); ordinary cross-talk does not.
    assert is_addressed("Bob", "proxy, check the DST bug") == "proxy, check the DST bug"
    assert is_addressed("Bob", "let's move on to the next item") is None
    # word-boundary: the voice gate is \bproxy\b — an EMBEDDED substring does NOT wake.
    assert is_addressed("Bob", "we deployed the proxyserver last night") is None
    # KNOWN LIMITATION (documented in is_addressed's docstring): the voice gate is only
    # word-bounded, so the common-noun phrase "proxy server" (a bare word 'proxy' followed by
    # 'server') DOES currently wake on voice — a bounded model confirm is the stated follow-up
    # refinement. We pin the ACTUAL behaviour so a future confirm-gate change is a visible diff.
    assert is_addressed("Bob", "the proxy server is down") is not None
    # chat requires the explicit @proxy handle; a common-noun 'proxy server' does NOT wake.
    assert is_addressed("Ann", "@proxy where is the helper?", is_chat=True) is not None
    assert is_addressed("Ann", "the proxy server is down", is_chat=True) is None
    assert is_addressed("Ann", "proxy is a good name", is_chat=True) is None
    # self: Proxy's own line never wakes it, in either channel (no self-wake).
    assert is_addressed("Proxy", "proxy the answer is 42") is None
    assert is_addressed("Proxy", "@proxy done", is_chat=True) is None
    # an empty line is never an address even if the speaker is human.
    assert is_addressed("Bob", "") is None


def test_addressed_line_runs_the_ask_and_replays_the_agents_recorded_intent() -> None:
    """On a wake the session runs run_ask with the verbatim line and, since the agent made no LIVE
    relay call this turn, REPLAYS the agent's OWN recorded ``to_meeting`` intent (the no-relay/file
    path) over the connection — honoring the agent's chosen medium (here 'say'), not our prose."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="internal note", sent=_say("It lives at util.ts:9")))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, where's the helper?", ts=2.0)
        await session.drain()

        assert wr.asks == ["proxy, where's the helper?"]  # verbatim ask, exactly one wake
        assert len(session.results) == 1
        # the recorded intent replayed over the connection with the agent's chosen medium:
        assert [s.medium for s in conn.sent] == ["say"]
        assert conn.sent[-1].content == "It lives at util.ts:9"  # the intent content, not result.text

    asyncio.run(_run())


def test_clean_turn_with_zero_intents_stays_silent_crosstalk() -> None:
    """CROSS-TALK: a clean turn (no error) where the agent recorded ZERO intents means the agent
    chose not to respond. The session must STAY SILENT — it must NOT invent a response from
    ``result.text``. Zero sends reach the room."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        # text present (an internal thought) but no recorded intents and no error -> chose silence:
        wr = _FakeWorkroom(result=_result(text="not really about me", sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy servers keep dropping", ts=2.0)
        await session.drain()

        assert wr.asks == ["proxy servers keep dropping"]  # it DID wake (word-bounded gate)
        assert len(session.results) == 1
        assert conn.sent == []   # ...but stayed silent: no cross-talk, no result.text spoken

    asyncio.run(_run())


def test_non_addressed_line_updates_notes_but_never_wakes() -> None:
    """A cross-talk line only materializes the transcript into the workroom (continuous feed);
    it must NOT run the expensive workroom or reach the room (idle costs nothing)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom()
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Alice", "kickoff for the release sync", ts=1.0)
        await session.on_line("Bob", "the deploy went out last night", ts=2.0)  # pure cross-talk
        await session.drain()

        assert wr.asks == []                 # never woke the workroom
        assert session.results == []
        assert conn.sent == []               # nothing reached the room
        assert len(wr.fed) == 2              # but BOTH lines were fed continuously
        # the rendered transcript carries both speakers + text (the up-to-date room a wake reads):
        assert "Alice" in wr.fed[-1] and "release sync" in wr.fed[-1]
        assert "Bob" in wr.fed[-1] and "deploy" in wr.fed[-1]

    asyncio.run(_run())


def test_stays_quiet_when_the_agent_acted_live_via_the_connection() -> None:
    """§5: when the agent reached the room DURING the turn (a to_meeting relay call recorded on the
    connection), the session must NOT also replay the recorded intents — no DOUBLE-SEND. The live
    relay wins even though ``result.sent`` also carries the same intent."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        async def _agent_acts(connection: Any) -> None:
            await connection.to_meeting("here's the answer", medium="chat")

        # The relay recorded the send on the connection AND result.sent carries the same intent
        # (the MCP server records even in relay mode). The session must NOT replay it a second time.
        wr = _FakeWorkroom(
            result=_result(text="here's the answer",
                           sent=[{"content": "here's the answer", "medium": "chat", "to": ""}]),
            connection=conn,
            on_run=_agent_acts,
        )
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, summarize it", ts=1.0)
        await session.drain()

        # exactly the one live chat send — the recorded intent was NOT replayed on top:
        assert [s.medium for s in conn.sent] == ["chat"]

    asyncio.run(_run())


def test_monitor_while_working_a_second_line_lands_while_the_first_wake_runs() -> None:
    """§3/§6: the loop keeps flowing while a wake works — a second line's transcript feed lands
    (and a second wake starts) while the first wake is still in flight. Proven by gating the first
    run_ask on an event the second line's feed sets."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        second_line_fed = asyncio.Event()
        first_may_finish = asyncio.Event()
        feeds_during_first_wake: list[int] = []

        class _MonitorWorkroom:
            def __init__(self) -> None:
                self.fed: list[str] = []
                self.asks: list[str] = []

            async def feed_transcript(self, md: str) -> None:
                self.fed.append(md)
                if "second question" in md:
                    second_line_fed.set()

            async def run_ask(self, ask: str) -> Any:
                self.asks.append(ask)
                if "first" in ask:
                    # Block until a later line has been fed WHILE this wake is in flight —
                    # proving the drain is not serialized behind the running turn.
                    await asyncio.wait_for(first_may_finish.wait(), timeout=2.0)
                    feeds_during_first_wake.append(len(self.fed))
                return SimpleNamespace(text=f"done: {ask}", error=None)

        wr = _MonitorWorkroom()
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, the first task please", ts=1.0)
        # while the first wake is mid-flight, a NEW human line arrives and is fed:
        await session.on_line("Ann", "proxy, the second question", ts=2.0)
        await asyncio.wait_for(second_line_fed.wait(), timeout=2.0)
        first_may_finish.set()
        await session.drain()

        # both lines woke the workroom; the second's transcript was materialized (>=2 feeds)
        # BEFORE the first wake finished — the room kept flowing while Proxy worked:
        assert feeds_during_first_wake and feeds_during_first_wake[0] >= 2
        assert len(wr.asks) == 2
        assert len(session.results) == 2

    asyncio.run(_run())


def test_fallback_speaks_an_honest_message_when_a_wake_errors_with_no_text() -> None:
    """§3.8 honest-degrade: a turn that produced an error result and no text speaks an honest
    'ran into a problem' line so the room is never left hanging (still zero live sends)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="", error="e2b timeout", sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, run the migration", ts=1.0)
        await session.drain()

        assert len(conn.sent) == 1 and conn.sent[-1].medium == "say"
        assert "problem" in conn.sent[-1].content.lower()

    asyncio.run(_run())


def test_a_wake_that_raises_is_a_no_op_the_meeting_survives() -> None:
    """§3.8: a run_ask that RAISES never crashes the meeting — the session swallows it and the
    room simply gets nothing that turn (no result recorded, no send, drain still completes)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(raise_on_run=True)
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, do the thing", ts=1.0)
        await session.drain()  # must not raise

        assert session.results == []   # nothing recorded from the failed turn
        assert conn.sent == []         # nothing reached the room
        # and the meeting keeps working — a subsequent good wake still replays the agent's intent:
        wr2 = _FakeWorkroom(result=_result(text="recovered", sent=_say("recovered")))
        session.workroom = wr2
        await session.on_line("Bob", "proxy, try again", ts=2.0)
        await session.drain()
        assert conn.sent and conn.sent[-1].content == "recovered"

    asyncio.run(_run())


def test_transcript_sync_failure_does_not_block_the_wake() -> None:
    """A feed_transcript that raises is an honest no-op (§3.8) — it must not stop the wake gate
    from still running the addressed line (the meeting continues)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        class _BadFeedWorkroom(_FakeWorkroom):
            async def feed_transcript(self, md: str) -> None:
                raise RuntimeError("sandbox write failed")

        wr = _BadFeedWorkroom(result=_result(text="internal", sent=_say("answered anyway")))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, still there?", ts=1.0)
        await session.drain()

        assert wr.asks == ["proxy, still there?"]  # the wake still ran despite the feed fault
        assert conn.sent and conn.sent[-1].content == "answered anyway"

    asyncio.run(_run())


def test_chat_line_wakes_only_on_atproxy_via_the_is_chat_flag() -> None:
    """The is_chat flag routes a line through the chat gate (@proxy), so a chat that merely
    contains the word 'proxy' does not wake, but '@proxy' does — end to end through on_line."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="internal", sent=_say("chat handled")))
        session = MeetingSession(workroom=wr, connection=conn)

        # a plain chat mention of the word proxy — NO wake:
        await session.on_line("Ann", "the proxy config is fine", ts=1.0, is_chat=True)
        await session.drain()
        assert wr.asks == []
        # an @proxy chat handle — wakes:
        await session.on_line("Ann", "@proxy what's the status?", ts=2.0, is_chat=True)
        await session.drain()
        assert wr.asks == ["@proxy what's the status?"]
        assert conn.sent and conn.sent[-1].content == "chat handled"

    asyncio.run(_run())
