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
import time
from types import SimpleNamespace
from typing import Any


def _result(*, text: str = "", error: str | None = None,
            sent: list[dict[str, Any]] | None = None,
            deliver_at: float = 0.0, ttft: float = 0.0,
            delivery_failed: bool = False) -> SimpleNamespace:
    """A stand-in WorkroomResult carrying the fields _handle reads: text, error, the agent's OWN
    recorded ``to_meeting`` intents (``sent``), and the relay-mode delivery signals ``deliver_at`` /
    ``ttft`` (set by the warm host ONLY when the agent actually spoke/called to_meeting — the robust
    signal the follow-up window opens off, since relay POSTs land on ``connection.sent`` asynchronously
    and may not have grown by the time run_ask returns). ``sent`` defaults to [] (no recorded intents)."""
    return SimpleNamespace(text=text, error=error, sent=list(sent or []),
                           deliver_at=deliver_at, ttft=ttft,
                           delivery_failed=delivery_failed)


class _Clock:
    """A controllable wall clock the follow-up window is measured on (``MeetingSession.now_fn``), so
    a test can advance real time past the short window without sleeping. ``advance`` moves it; calling
    it returns the current value — the exact ``time.monotonic`` shape the production default uses."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _say(content: str) -> list[dict[str, Any]]:
    """One recorded 'say' intent — the common no-relay channel choice, so a scripted turn's result
    replays as a spoken line over the connection (the agent's OWN choice, not our prose)."""
    return [{"content": content, "medium": "say", "to": ""}]


class _FakeSpeak:
    """Mirrors SpeakPipe's barge-in surface the reactive loop reads/drives: ``speaking`` (the cut
    guard) and ``cut`` (the barge-in primitive). ``speaking`` is a mutable flag a test sets to stage
    Proxy mid-utterance; ``cut`` records the stop and clears it, like the real pipe."""

    def __init__(self, *, speaking: bool = False) -> None:
        self.speaking = speaking
        self.cuts = 0

    async def cut(self) -> None:
        self.cuts += 1
        self.speaking = False


class _FakeConnection:
    """Records what actually reached the room, mirroring MeetingConnection.sent / to_meeting —
    including the ``spoken`` log (voice lines only) the reactive loop reads for self-echo
    suppression, and the barge-in surface (``speak``/``barge_in``/``begin_turn`` + ``cut_latched``)
    it drives on a human talking over Proxy — recorded exactly as the real connection does."""

    def __init__(self, *, speaking: bool = False, audible_horizon: float = 0.0) -> None:
        self.sent: list[SimpleNamespace] = []
        self.spoken: list[tuple[float, str]] = []
        self.speak = _FakeSpeak(speaking=speaking)
        self.cut_latched = False
        self.begin_turns = 0
        #: The room's audible-end horizon (on the session's ``now_fn`` clock) the follow-up window
        #: anchors past — mirrors ``MeetingConnection.audible_until``. 0.0 ⇒ not speaking / unknown.
        self._audible_horizon = audible_horizon

    def audible_until(self) -> float:
        return self._audible_horizon

    async def to_meeting(self, content: str, medium: str = "say", to: str | None = None) -> Any:
        rec = SimpleNamespace(medium=medium, ok=True, detail="", content=content)
        self.sent.append(rec)
        if medium in ("say", "speak", "voice") and content.strip():
            self.spoken.append((time.time(), content))
        return rec

    async def barge_in(self) -> None:
        self.cut_latched = True
        await self.speak.cut()

    def begin_turn(self) -> None:
        self.cut_latched = False
        self.begin_turns += 1


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
        self.deltas: list[str] = []   # the per-wake transcript delta each run_ask received
        self._result = result if result is not None else _result(text="ok", sent=_say("ok"))
        self._connection = connection
        self._on_run = on_run
        self._raise_on_run = raise_on_run

    async def feed_transcript(self, md: str) -> None:
        self.fed.append(md)

    async def run_ask(self, ask: str, *, delta: str = "") -> Any:
        self.asks.append(ask)
        self.deltas.append(delta)
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
    """§5: when the agent reached the room DURING the turn via the live relay, the session must NOT
    also replay — no DOUBLE-SEND. In RELAY mode the in-sandbox MCP POSTs each call live to the
    connection and records NOTHING locally, so ``result.sent`` is EMPTY — there is simply nothing to
    replay. (We key off ``result.sent``, never the shared ``connection.sent`` counter — see the
    overlapping-wakes regression below.)"""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        async def _agent_acts(connection: Any) -> None:
            await connection.to_meeting("here's the answer", medium="chat")

        # Relay mode: the live send lands on the connection; the MCP records nothing locally, so
        # result.sent is EMPTY. The session must not manufacture a second send.
        wr = _FakeWorkroom(
            result=_result(text="here's the answer", sent=[]),
            connection=conn,
            on_run=_agent_acts,
        )
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, summarize it", ts=1.0)
        await session.drain()

        # exactly the one live chat send — nothing replayed on top:
        assert [s.medium for s in conn.sent] == ["chat"]

    asyncio.run(_run())


def test_overlapping_wakes_both_deliver_no_dropped_response() -> None:
    """Regression (live-meeting sim): two people address Proxy close together → two OVERLAPPING wakes.
    Each must deliver its OWN response. The old code decided "did I already deliver?" from the shared
    ``connection.sent`` counter, so the second wake saw the FIRST wake's replay grow it and wrongly
    concluded it had delivered — silently DROPPING its own intent (incl. an offer). Keying off the
    per-wake ``result.sent`` fixes it: both land."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        class _WR:
            def __init__(self) -> None:
                self.fed: list[str] = []
                self.asks: list[str] = []

            async def feed_transcript(self, md: str) -> None:
                self.fed.append(md)

            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                await asyncio.sleep(0.05)  # let the two wakes overlap in flight
                which = "first" if "first" in ask else "second"
                return _result(sent=_say(f"{which} answer"))

        session = MeetingSession(workroom=_WR(), connection=conn)
        await session.on_line("Bob", "proxy, the first thing", ts=1.0)
        await session.on_line("Ann", "proxy, the second thing", ts=2.0)
        await session.drain()

        # BOTH overlapping wakes delivered — neither was dropped by the other's replay:
        assert sorted(s.content for s in conn.sent) == ["first answer", "second answer"]

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

            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
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


def test_error_with_no_intent_speaks_bare_apology_never_internal_prose() -> None:
    """BUG-7 (soft Law 2): an errored turn with NO recorded ``to_meeting`` intent speaks ONE bare,
    honest apology — NEVER the agent's last assistant prose (``result.text``). That prose is internal
    scratchpad the agent did NOT choose to say to the room, so surfacing it would put words in Proxy's
    mouth it never picked. (An intent, which the agent DID choose, is replayed — the next test.)"""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        drafted = "I drafted the double-booking fix on branch fix/x and was validating the migration"
        wr = _FakeWorkroom(result=_result(text=drafted, error="turn did not complete", sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, implement the fix", ts=1.0)
        await session.drain()

        assert len(conn.sent) == 1 and conn.sent[-1].medium == "say"
        # the bare apology is spoken; the internal prose is NEVER put in Proxy's mouth
        assert "problem" in conn.sent[-1].content.lower()
        assert "double-booking fix" not in conn.sent[-1].content

    asyncio.run(_run())


def test_error_with_a_recorded_intent_delivers_that_intent_not_an_apology() -> None:
    """BUG-7 companion: an errored turn that DID record a ``to_meeting`` intent (the agent's own
    choice for the room) delivers THAT intent honoring its medium — the error branch (bare apology)
    is never reached, so the agent's chosen words carry the turn."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        chosen = {"content": "Here's the migration draft — take a look.", "medium": "chat", "to": ""}
        wr = _FakeWorkroom(result=_result(text="internal notes", error="turn did not complete",
                                          sent=[chosen]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, implement the fix", ts=1.0)
        await session.drain()

        assert len(conn.sent) == 1
        assert conn.sent[-1].medium == "chat"
        assert conn.sent[-1].content == "Here's the migration draft — take a look."
        # neither the bare apology nor the internal prose is spoken
        assert "problem" not in conn.sent[-1].content.lower()
        assert "internal notes" not in conn.sent[-1].content

    asyncio.run(_run())


def test_ask_reply_continue_an_unaddressed_answer_resumes_the_task() -> None:
    """ASK → ANSWER → CONTINUE (the headline path): Proxy is addressed, asks the room a clarifying
    question and delivers nothing else → the NEXT substantive human line, which does NOT name Proxy,
    is treated as the answer and wakes Proxy to CONTINUE the same task with the prior Q + this A. The
    normal name-gate would have IGNORED that reply; the pending-question latch is what carries it."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        class _WR:
            def __init__(self) -> None:
                self.fed: list[str] = []
                self.asks: list[str] = []

            async def feed_transcript(self, md: str) -> None:
                self.fed.append(md)

            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                # turn 1 (the address) ends on a QUESTION; turn 2 (the continuation) delivers the work
                if "Earlier you asked" in ask:  # the CONTINUATION prompt → deliver the real result
                    return _result(sent=_say("Done — patched the cal.com double-booking bug."))
                return _result(sent=_say("Which repo should I patch — cal.com or the fork?"))

        wr = _WR()
        session = MeetingSession(workroom=wr, connection=conn)

        # 1) a human addresses Proxy → Proxy asks a clarifying question and stops
        await session.on_line("Bob", "proxy, fix the double-booking bug", ts=1.0)
        await session.drain()
        assert conn.sent[-1].content.endswith("?")            # Proxy asked the room
        assert session._pending_question is not None          # ...and latched the pending question

        # 2) a human REPLIES WITHOUT naming Proxy — normally ignored by the name-gate
        assert __import__("control_plane.meeting_session", fromlist=["is_addressed"]).is_addressed(
            "Ann", "cal.com please") is None
        await session.on_line("Ann", "cal.com please", ts=5.0)
        await session.drain()

        # the reply woke Proxy as a CONTINUATION carrying BOTH the prior question and the answer:
        assert len(wr.asks) == 2
        cont = wr.asks[1]
        assert "Earlier you asked" in cont and "which repo" in cont.lower()
        assert "cal.com please" in cont and "Ann" in cont
        # Proxy then delivered the real result, and the latch is cleared (task resumed):
        assert conn.sent[-1].content == "Done — patched the cal.com double-booking bug."
        assert session._pending_question is None

    asyncio.run(_run())


def test_continuation_does_not_fire_when_last_turn_ended_on_a_statement() -> None:
    """The CONTINUATION latch is set ONLY when Proxy ends its turn on a question. A completed answer
    (a statement) latches nothing — so an un-addressed line is never carried as a task CONTINUATION.

    FIX 3 (F1): a delivered turn opens the short follow-up window, within which an un-addressed line
    IS routed to judgment (a separate path). To isolate the continuation contract from that window,
    the un-addressed cross-talk here arrives AFTER the window has expired — proving no continuation
    latch fires (the name-gate is in sole control once the window closes)."""
    from control_plane.meeting_session import _FOLLOW_UP_WINDOW_S, MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(sent=_say("It lives at util.ts:9.")))  # a statement, no '?'
        clock = _Clock()
        session = MeetingSession(workroom=wr, connection=conn, now_fn=clock)

        await session.on_line("Bob", "proxy, where's the helper?", ts=1.0)
        await session.drain()
        assert session._pending_question is None              # nothing to continue (a statement)

        # ordinary cross-talk AFTER the follow-up window closed (wall clock advanced past it) → no
        # continuation, no wake. The window is WALL-clock now (anchored past the audio horizon), so
        # the test advances real time rather than the meeting-transcript ts.
        clock.advance(_FOLLOW_UP_WINDOW_S + 5.0)
        await session.on_line("Ann", "great, thanks everyone", ts=2.0)
        await session.drain()
        assert wr.asks == ["proxy, where's the helper?"]      # NOT re-woken by the un-addressed line

    asyncio.run(_run())


def test_continuation_ignores_blips_then_fires_on_the_real_reply() -> None:
    """A sub-onset blip ('um') is not an answer: the pending question stays live for the REAL reply.
    The latch is consumed once, by the first substantive human line."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        class _WR(_FakeWorkroom):
            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                if "Earlier you asked" in ask:
                    return _result(sent=_say("shipped it"))
                return _result(sent=_say("Postgres or SQLite for the store?"))

        wr = _WR()
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, wire up persistence", ts=1.0)
        await session.drain()
        assert session._pending_question is not None

        await session.on_line("Ann", "um", ts=2.0)            # a blip — not the answer
        await session.drain()
        assert len(wr.asks) == 1                              # did NOT continue on the blip
        assert session._pending_question is not None          # latch still standing

        await session.on_line("Ann", "use Postgres", ts=3.0)  # the real reply (a substantive line)
        await session.drain()
        assert len(wr.asks) == 2 and "Postgres" in wr.asks[1]
        assert session._pending_question is None

    asyncio.run(_run())


def test_continuation_expires_and_does_not_hijack_a_much_later_line() -> None:
    """A pending question is only live for a bounded window. A line arriving after the timeout is NOT
    hijacked as an answer — the moment passed and the name-gate is back in sole control."""
    from control_plane.meeting_session import (
        _CONTINUE_TIMEOUT_S,
        _FOLLOW_UP_WINDOW_S,
        MeetingSession,
    )

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(sent=_say("Which environment — staging or prod?")))
        clock = _Clock()
        session = MeetingSession(workroom=wr, connection=conn, now_fn=clock)

        await session.on_line("Bob", "proxy, run the deploy", ts=1.0)
        await session.drain()
        assert session._pending_question is not None

        # a wholly unrelated line, well AFTER both windows — must not be treated as the answer. The
        # continuation latch expires on the meeting-clock ts; the (question-turn) follow-up window
        # expires on the WALL clock, so advance both past their windows to isolate the no-hijack.
        clock.advance(_FOLLOW_UP_WINDOW_S + 10.0)
        await session.on_line("Ann", "anyway, lunch plans?", ts=1.0 + _CONTINUE_TIMEOUT_S + 10.0)
        await session.drain()
        assert len(wr.asks) == 1                              # not continued
        assert session._pending_question is None              # expired latch cleared

    asyncio.run(_run())


def test_a_named_readdress_supersedes_a_pending_question() -> None:
    """An explicit re-address always wins: it starts a fresh turn on the new line and clears any
    pending question (the continuation branch is only for UN-addressed replies)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()

        class _WR(_FakeWorkroom):
            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                if "changelog" in ask:
                    return _result(sent=_say("Changelog drafted."))
                return _result(sent=_say("Which milestone?"))

        wr = _WR()
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, draft the release notes", ts=1.0)
        await session.drain()
        assert session._pending_question is not None

        # a NAMED re-address on a new task — runs verbatim as its own wake, NOT a continuation:
        await session.on_line("Bob", "proxy, actually just draft the changelog", ts=3.0)
        await session.drain()
        assert wr.asks[1] == "proxy, actually just draft the changelog"  # verbatim, not a continuation
        assert "Earlier you asked" not in wr.asks[1]
        assert session._pending_question is None

    asyncio.run(_run())


# ── FOLLOW-UP WINDOW (F1) ─────────────────────────────────────────────────────────


def test_follow_up_window_routes_an_unaddressed_line_after_a_delivered_turn() -> None:
    """F1: after a turn that DELIVERED (ended on a statement — no pending question), a substantive
    human line WITHIN the short window is ROUTED to the model's judgment even without 'proxy'. We
    test the ROUTING (run_ask was called with the line verbatim), not the model — the model's own
    [SILENT] verdict is the over-fire guard, exercised separately below."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        # A delivered STATEMENT turn (no trailing '?') so the pending-question latch is NOT involved —
        # this isolates the follow-up window from the ASK→CONTINUE path.
        wr = _FakeWorkroom(result=_result(sent=_say("The build is green on main.")))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, is the build green?", ts=1.0)
        await session.drain()
        assert session._pending_question is None            # a statement — no continuation latch
        assert session._follow_up_until > 0.0               # ...but the follow-up window opened

        # a follow-up that does NOT name Proxy, inside the window → routed to judgment (a wake ran)
        assert __import__("control_plane.meeting_session", fromlist=["is_addressed"]).is_addressed(
            "Bob", "cool, the audio was choppy last time") is None
        await session.on_line("Bob", "cool, the audio was choppy last time", ts=2.0)
        await session.drain()
        assert len(wr.asks) == 2, "the in-window line woke the model's judgment (no name needed)"
        assert wr.asks[1] == "cool, the audio was choppy last time", "routed verbatim (normal prompt)"

    asyncio.run(_run())


def test_follow_up_window_expires_and_an_unaddressed_line_after_it_does_not_wake() -> None:
    """F1: the window is SHORT. A line arriving AFTER it expires is pure cross-talk again — the
    name-gate is back in sole control, so it does NOT wake (expired silently, no response)."""
    from control_plane.meeting_session import _FOLLOW_UP_WINDOW_S, MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(sent=_say("The build is green on main.")))
        clock = _Clock()
        session = MeetingSession(workroom=wr, connection=conn, now_fn=clock)

        await session.on_line("Bob", "proxy, is the build green?", ts=1.0)
        await session.drain()
        assert session._follow_up_until > 0.0

        # advance WALL time past the window, then an un-addressed line → not routed, no wake:
        clock.advance(_FOLLOW_UP_WINDOW_S + 5.0)
        await session.on_line("Bob", "anyway lets grab lunch", ts=2.0)
        await session.drain()
        assert len(wr.asks) == 1, "a line after the window expired did NOT wake"
        assert session._follow_up_until == 0.0, "the expired window was cleared"

    asyncio.run(_run())


def test_a_silent_turn_opens_no_follow_up_window() -> None:
    """F1 guard: a true SILENT turn (cross-talk the agent judged not-for-it — zero delivery) must
    NOT open the window, else every incidental 'proxy' mention would route the next lines. Here the
    incidental mention runs a silent turn (sent=[], no relay), so the FOLLOWING un-addressed line
    stays pure cross-talk and does not wake."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="[SILENT]", sent=[]))  # silence, zero delivery
        session = MeetingSession(workroom=wr, connection=conn)

        # an incidental 'proxy server' mention wakes the gate but the agent stays silent (sent=[])
        await session.on_line("Bob", "our proxy server keeps dropping", ts=1.0)
        await session.drain()
        assert len(wr.asks) == 1 and conn.sent == []        # ran, delivered nothing
        assert session._follow_up_until == 0.0, "a silent turn opened NO window"

        # the next un-addressed line is therefore NOT routed (name-gate in sole control):
        await session.on_line("Bob", "and the DB is slow too", ts=3.0)
        await session.drain()
        assert len(wr.asks) == 1, "no window ⇒ the following cross-talk line did not wake"

    asyncio.run(_run())


def test_barge_in_opens_the_follow_up_window_so_the_interrupting_line_reaches_judgment() -> None:
    """F1 + Law 3: being interrupted IS mid-exchange. When a human talks over Proxy (a barge-in
    fires), the window opens — so the interrupting line itself, and the next lines within the window,
    reach the model's judgment WITHOUT the wake word. The founder's 'wait, hold on that's not right'
    is almost always still to Proxy; the model's [SILENT] verdict remains the over-fire guard."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        # Proxy is mid-utterance (speaking True) so the human line is a real barge-in.
        conn = _FakeConnection(speaking=True)
        wr = _FakeWorkroom(result=_result(sent=_say("Understood — I'll re-check that.")))
        session = MeetingSession(workroom=wr, connection=conn)

        # a real interjection (≥2 tokens) over active speech, NOT naming Proxy:
        assert __import__("control_plane.meeting_session", fromlist=["is_addressed"]).is_addressed(
            "Bob", "wait hold on that is not right") is None
        await session.on_line("Bob", "wait hold on that is not right", ts=10.0)
        await session.drain()

        assert conn.speak.cuts == 1, "the barge-in cut Proxy's speech (Law 3)"
        assert session._follow_up_until > 0.0, "the barge-in opened the follow-up window"
        # the interrupting line itself reached the model's judgment (routed as a wake, no name):
        assert wr.asks == ["wait hold on that is not right"], "the interrupting line reached judgment"

    asyncio.run(_run())


def test_relay_mode_delivered_turn_opens_the_follow_up_window() -> None:
    """F1 live-path REGRESSION (the founder run): in RELAY mode the in-sandbox MCP POSTs each spoken
    sentence live, so ``result.sent`` is EMPTY (nothing recorded locally to replay) — yet the room
    DID hear the answer. The old window gate keyed ONLY on ``len(connection.sent) > sent_before``,
    which races the sandbox's async relay POSTs (they can land AFTER run_ask returns), so live the
    window NEVER opened and the founder's un-addressed follow-up ('Introduce yourself…') got no reply.

    The fix opens the window off the ROBUST in-result delivery signal (``deliver_at``/``ttft`` — set
    by the warm host only when the agent actually spoke this turn). Here we fake the EXACT relay-mode
    result shape live produces (sent=[], deliver_at>0, ttft>0, and NO connection.sent growth) and
    assert the window opens and the next un-addressed line is routed."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        # RELAY-mode shape EXACTLY as the live wake record showed: nothing recorded locally
        # (sent=[]), the connection's sent counter did NOT grow this turn (the POSTs are async /
        # elsewhere), but the agent DID deliver — deliver_at + ttft are > 0.
        wr = _FakeWorkroom(result=_result(text="Cova is an AI interior design app.",
                                          sent=[], deliver_at=7.28, ttft=2.81))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, what is cova?", ts=58.0)
        await session.drain()
        assert conn.sent == [], "relay mode records nothing locally — the session replays nothing"
        assert session._follow_up_until > 0.0, "the relay-mode delivery opened the window (deliver_at)"

        # the founder's un-addressed follow-up, inside the window → routed to judgment (a wake ran):
        assert __import__("control_plane.meeting_session", fromlist=["is_addressed"]).is_addressed(
            "Bob", "Introduce yourself in exactly one sentence") is None
        await session.on_line("Bob", "Introduce yourself in exactly one sentence", ts=106.0)
        await session.drain()
        assert len(wr.asks) == 2, "the relay-mode window routed the un-addressed follow-up (the live bug)"
        assert wr.asks[1] == "Introduce yourself in exactly one sentence"

    asyncio.run(_run())


def test_follow_up_window_is_anchored_past_the_audible_horizon() -> None:
    """F1: the answer keeps PLAYING for seconds after the wake record lands (synth outruns playback),
    and the founder replies just AFTER it finishes. So the window must cover [audio-end, audio-end +
    _FOLLOW_UP_WINDOW_S] — anchored past the connection's audible horizon, NOT from when the record
    landed. Here the horizon is well in the future (a long answer still playing); a follow-up that
    arrives AFTER the record-land instant but BEFORE audio-end + window must still be inside the
    window — which is only true if the anchor is the horizon, not 'now'."""
    from control_plane.meeting_session import _FOLLOW_UP_WINDOW_S, MeetingSession

    async def _run() -> None:
        clock = _Clock(t=1000.0)
        # The room will still be audibly playing until 1000 + 18s (an ~18s answer whose PCM the
        # synth returned instantly): the record lands at t=1000 but audio-end is at t=1018.
        conn = _FakeConnection(audible_horizon=1018.0)
        wr = _FakeWorkroom(result=_result(sent=_say("A long grounded answer.")))
        session = MeetingSession(workroom=wr, connection=conn, now_fn=clock)

        await session.on_line("Bob", "proxy, walk me through the pipeline", ts=1.0)
        await session.drain()
        # The window must extend to audio-end (1018) + the window width — NOT to now (1000) + width.
        assert session._follow_up_until == 1018.0 + _FOLLOW_UP_WINDOW_S, "anchored past the horizon"

        # A follow-up that lands AFTER the naive now+window (1000+15=1015) but before audio-end+window
        # (1018+15=1033) must STILL be inside — proving the horizon anchor (the old now-anchor would
        # have already closed it, exactly the founder's 'replied one second after it finished' miss).
        clock.advance(20.0)  # t=1020: past now+window, inside horizon+window
        assert __import__("control_plane.meeting_session", fromlist=["is_addressed"]).is_addressed(
            "Bob", "nice, how are you doing today") is None
        await session.on_line("Bob", "nice, how are you doing today", ts=2.0)
        await session.drain()
        assert len(wr.asks) == 2, "the follow-up just after audio-end was still inside the window"

    asyncio.run(_run())


def test_render_transcript_windows_to_recent_lines_with_an_elision_header() -> None:
    """FW-2: the workroom feed is windowed to the last _TRANSCRIPT_WINDOW lines (O(1) per write, not
    O(N)); older lines are elided with an honest header rather than re-uploaded every line."""
    from control_plane.meeting_session import _TRANSCRIPT_WINDOW, MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="", sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        # feed more than one window of non-addressing lines (no wake)
        for i in range(_TRANSCRIPT_WINDOW + 50):
            await session.on_line("Bob", f"line {i}", ts=float(i))
        rendered = wr.fed[-1]
        # only the window's worth of body lines are present, plus the elision header
        assert "earlier line(s) elided" in rendered
        assert "[5] Bob: line 5" not in rendered    # an early line is dropped (exact, ts-anchored)
        assert f"Bob: line {_TRANSCRIPT_WINDOW + 49}" in rendered  # the newest line is present
        assert rendered.count("] Bob:") == _TRANSCRIPT_WINDOW

    asyncio.run(_run())


def test_each_wake_inlines_only_the_transcript_delta_since_the_last_wake() -> None:
    """ACCEPTANCE (SPEC §3): each wake's FRESH input is only the delta since the last wake — the new
    lines only, never a re-sent recon window. The whole transcript thus accumulates in the warm
    session's cache turn-over-turn; only the delta + the ask are fresh per wake. Here: lines flow, a
    wake fires, MORE lines flow, a second wake fires — the second wake's delta must contain ONLY the
    lines said after the first wake (the earlier ones are already resident from the first wake)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="", sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        # Pre-wake chatter, then the FIRST wake — its delta is the whole meeting-so-far.
        await session.on_line("Ann", "the auth token lives in settings.py", ts=1.0)
        await session.on_line("Bob", "and it rotates hourly", ts=2.0)
        await session.on_line("Cy", "proxy, note that", ts=3.0)
        await session.drain()

        # More chatter, then a SECOND wake — its delta must be ONLY the lines since the first wake.
        await session.on_line("Dee", "let's move to the roadmap", ts=4.0)
        await session.on_line("Ann", "proxy, what was the auth token detail?", ts=5.0)
        await session.drain()

        assert len(wr.deltas) == 2
        first, second = wr.deltas[0], wr.deltas[1]
        # First wake: the whole meeting-so-far (the fact is delivered into the cache here, once).
        assert "the auth token lives in settings.py" in first
        assert "proxy, note that" in first
        # Second wake: ONLY the delta since the first wake — the early fact is NOT re-sent (it's
        # already resident in the cache from wake 1); only the new lines are fresh.
        assert "the auth token lives in settings.py" not in second
        assert "and it rotates hourly" not in second
        assert "let's move to the roadmap" in second          # new since wake 1
        assert "what was the auth token detail?" in second     # the addressing line, new
        # No overlap: every line appears in exactly one wake's delta (accumulation, not re-window).
        assert "let's move to the roadmap" not in first

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


def test_is_self_echo_matches_proxy_speech_label_independently() -> None:
    """_is_self_echo recognizes Proxy's own voice returning on the transcript by matching WHAT Proxy
    said (not the speaker label), within a time window, with a strict length + containment bar."""
    from control_plane.meeting_session import _is_self_echo

    now = 1_000.0
    spoken = [(now - 2.0, "The entry point is Command.main() in src/click/core.py at line 1477.")]

    # a near-verbatim echo (STT-style: lowercased, punctuation gone) — matched
    assert _is_self_echo("the entry point is command main in src click core py", spoken, now) is True
    # only a partial span of what Proxy said came back — still matched (containment on incoming)
    assert _is_self_echo("entry point is command main", spoken, now) is True
    # an unrelated human line — NOT an echo
    assert _is_self_echo("can we move on to the roadmap item", spoken, now) is False
    # too short to be a confident echo (< min tokens) — never suppressed
    assert _is_self_echo("entry point", spoken, now) is False
    # outside the echo window — a human genuinely saying it much later is NOT suppressed
    assert _is_self_echo(
        "the entry point is command main in src click core py", spoken, now + 100.0) is False


def test_self_echo_on_a_human_mic_is_relabeled_and_never_rewakes() -> None:
    """The headphones-optional guarantee: when Proxy's own voice echoes from the speakers into a
    human's mic, it returns MISLABELED as that human (and may contain the word 'proxy'). It must be
    recognized as Proxy's own echo, recorded as Proxy in the transcript, and NEVER re-wake Proxy."""
    from control_plane.meeting_session import PROXY_SPEAKER, MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(
            result=_result(sent=_say("Proxy here — the entry point is Command.main in core.py")),
            connection=conn,
        )
        session = MeetingSession(workroom=wr, connection=conn)

        # a human addresses Proxy → wakes → Proxy answers (its say is logged in conn.spoken)
        await session.on_line("Bob", "proxy, where is the entry point?", ts=1.0)
        await session.drain()
        assert wr.asks == ["proxy, where is the entry point?"]
        assert len(conn.spoken) == 1  # Proxy's spoken answer was recorded as the echo reference

        asks_before = list(wr.asks)
        # NO headphones: Proxy's answer echoes into Bob's mic → arrives labeled "Bob" and CONTAINS
        # 'proxy' (would re-wake under the speaker-name filter alone → an infinite loop).
        await session.on_line(
            "Bob", "proxy here the entry point is command main in core py", ts=3.0)
        await session.drain()

        assert wr.asks == asks_before  # did NOT re-wake — the self-echo loop is broken
        transcript = wr.fed[-1]
        assert f"{PROXY_SPEAKER}: proxy here the entry point" in transcript  # attributed to Proxy
        assert "Bob: proxy here the entry point" not in transcript          # not to the human

    asyncio.run(_run())


def test_short_human_line_sharing_a_word_is_not_mistaken_for_echo() -> None:
    """The strict bar protects a genuine human ask that merely reuses a word or two of Proxy's
    recent speech: it is NOT swallowed as an echo — it still wakes normally."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        conn.spoken.append((time.time(), "The entry point is Command.main in src/click/core.py"))
        wr = _FakeWorkroom(result=_result(sent=_say("ok")), connection=conn)
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Ann", "proxy, can you open the entry point file?", ts=2.0)
        await session.drain()
        assert wr.asks == ["proxy, can you open the entry point file?"]  # genuine ask, still woke

    asyncio.run(_run())


def test_chat_is_never_echo_suppressed() -> None:
    """Chat is text and cannot echo acoustically, so the echo guard skips it entirely — a human who
    types @proxy while quoting Proxy's exact words still wakes Proxy."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        conn.spoken.append((time.time(), "the entry point is command main in core py"))
        wr = _FakeWorkroom(result=_result(sent=_say("ok")), connection=conn)
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line(
            "Ann", "@proxy the entry point is command main in core py", ts=2.0, is_chat=True)
        await session.drain()
        assert wr.asks == ["@proxy the entry point is command main in core py"]

    asyncio.run(_run())


def test_human_line_during_speech_triggers_a_barge_in_cut() -> None:
    """Law 3: a HUMAN talking while Proxy is mid-utterance stops its speech at once — the reactive
    loop calls ``connection.barge_in`` (which cuts the pipe). This holds whether or not the line is an
    address: a human simply talking over Proxy silences it. The cut runs BEFORE the line is fed.

    FIX 3 (F1): being interrupted IS mid-exchange, so the barge-in also OPENS the follow-up window and
    the interrupting line reaches the model's judgment name-free. So this line DOES wake — but the
    fake workroom returns a SILENT result (``sent=[]``), so no new spoken output is produced; the
    barge-in's job (cut the interrupted speech) is unchanged."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)  # Proxy is audibly speaking
        wr = _FakeWorkroom(result=_result(sent=[]))  # the judged turn stays silent
        session = MeetingSession(workroom=wr, connection=conn)

        # a human interjects while Proxy speaks (not even an address — just talking over it)
        await session.on_line("Bob", "hold on, that's not right", ts=1.0)
        await session.drain()

        assert conn.speak.cuts == 1          # speech was cut
        assert session._follow_up_until > 0.0  # the barge-in opened the follow-up window (F1)
        # the interrupting line reached the model's judgment (routed as a wake, no name needed) —
        # and the model judged silence here, so nothing new was said:
        assert wr.asks == ["hold on, that's not right"]
        assert conn.sent == []               # a silent judged turn delivered nothing new

    asyncio.run(_run())


def test_sub_onset_blip_during_speech_does_not_cut() -> None:
    """The debounce (Law 3, honest): a sub-onset STT blip — a lone filler token — is NOT a real
    interjection and must NOT cut Proxy mid-sentence. Proxy never flinches at noise."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)
        wr = _FakeWorkroom(result=_result(sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "um", ts=1.0)      # a single-token blip
        await session.on_line("Bob", "  ", ts=1.1)       # pure whitespace — no tokens
        await session.drain()

        assert conn.speak.cuts == 0          # no cut on a blip
        assert conn.cut_latched is False

    asyncio.run(_run())


def test_barge_in_only_fires_when_proxy_is_actually_speaking() -> None:
    """No speech in flight ⇒ nothing to cut. A human line while Proxy is idle never calls the cut
    (the pipe's ``speaking`` guard is False), so we don't cut on nothing."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=False)  # Proxy is idle
        wr = _FakeWorkroom(result=_result(sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "some substantial human sentence here", ts=1.0)
        await session.drain()

        assert conn.speak.cuts == 0

    asyncio.run(_run())


def test_proxy_self_echo_never_barges_in_on_itself() -> None:
    """Proxy's own voice echoing back on a no-headphones mic (relabeled to Proxy by the echo guard)
    must NOT trigger a self-barge-in — Proxy cutting its own speech would be a bug (§3.6)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)
        # Proxy has said this; the echo returns mislabeled as a human but reproduces it verbatim.
        conn.spoken.append((time.time(), "the entry point is command main in src click core py"))
        wr = _FakeWorkroom(result=_result(sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line(
            "Bob", "the entry point is command main in src click core py", ts=1.0)
        await session.drain()

        assert conn.speak.cuts == 0          # the echo is Proxy's own voice — no self-barge-in
        assert conn.cut_latched is False

    asyncio.run(_run())


def test_a_new_wake_clears_the_cut_latch_so_its_speech_flows() -> None:
    """After a barge-in latches out the interrupted turn, the NEXT wake's delivery begins with a clean
    latch (``begin_turn``) so its spoken output is not wrongly suppressed.

    FIX 3 (F1): a barge-in now ALSO routes the interrupting line to judgment — so that judged turn's
    ``begin_turn`` is the new-wake that clears the latch. Here the interrupting line is judged SILENT
    (``sent=[]``), and a following named address delivers the real answer with a clean latch."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)

        class _WR(_FakeWorkroom):
            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                if "what's the fix" in ask:      # the named address delivers the real answer
                    return _result(sent=_say("here is the fresh answer"))
                return _result(sent=[])          # the interrupting line is judged silent

        wr = _WR()
        session = MeetingSession(workroom=wr, connection=conn)

        # a human barges in (cut fires); the interrupting line is judged silent → begin_turn ran but
        # produced nothing, so the cut of the interrupted turn stands and no new speech flowed.
        await session.on_line("Bob", "wait, stop for a second", ts=1.0)
        await session.drain()
        assert conn.speak.cuts == 1
        assert conn.sent == []               # the judged interrupting line delivered nothing

        # now a real address wakes Proxy → the wake clears the latch before delivering its intent
        await session.on_line("Bob", "proxy, what's the fix?", ts=2.0)
        await session.drain()
        assert conn.begin_turns >= 1
        assert conn.cut_latched is False
        assert conn.sent and conn.sent[-1].content == "here is the fresh answer"

    asyncio.run(_run())


def test_barge_in_cut_fault_never_crashes_the_meeting() -> None:
    """§3.8 never-throw: if the barge-in cut itself raises, the drain still completes and the line is
    still fed — the meeting survives a faulty cut."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)

        async def _boom() -> None:
            raise RuntimeError("cut blew up")

        conn.barge_in = _boom  # type: ignore[method-assign]
        wr = _FakeWorkroom(result=_result(sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "this is a real interjection", ts=1.0)
        await session.drain()  # must not raise

        assert len(wr.fed) == 1  # the line was still fed despite the cut fault

    asyncio.run(_run())


# ── BUG 3: partial-transcript barge-in (cut on human onset, not the ~8s-late final) ──


def test_partial_transcript_line_cuts_active_speech() -> None:
    """BUG 3: a NON-FINAL (partial) line — the earliest 'a human started talking' signal — cuts
    Proxy's active speech at once via ``on_partial``, without waking, feeding, or logging. On the
    live path only the ~8s-late FINAL line was dispatched, so a barge-in never fired in time."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)          # Proxy is mid-utterance
        wr = _FakeWorkroom(result=_result(sent=[]))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_partial("Bob", "wait hold on that's", ts=1.0)  # a real partial onset

        assert conn.speak.cuts == 1          # speech cut on the partial (not the late final)
        assert conn.cut_latched is True
        assert wr.asks == []                 # a partial never wakes
        assert wr.fed == []                  # a partial is never fed as transcript

    asyncio.run(_run())


def test_partial_sub_onset_blip_does_not_cut() -> None:
    """BUG 3 debounce: a sub-onset partial blip (a lone filler token) must NOT cut — same floor as
    the final path, so Proxy never flinches at STT noise (Law 3)."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)
        session = MeetingSession(workroom=_FakeWorkroom(result=_result(sent=[])), connection=conn)
        await session.on_partial("Bob", "um", ts=1.0)   # one token — below the barge floor
        assert conn.speak.cuts == 0

    asyncio.run(_run())


def test_partial_when_idle_or_self_echo_never_cuts() -> None:
    """BUG 3: a partial while Proxy is idle cuts nothing; a partial that reproduces Proxy's own recent
    speech (its echo on a no-headphones mic) is never a self-barge-in."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        idle = _FakeConnection(speaking=False)
        s1 = MeetingSession(workroom=_FakeWorkroom(result=_result(sent=[])), connection=idle)
        await s1.on_partial("Bob", "a real sentence being spoken", ts=1.0)
        assert idle.speak.cuts == 0          # nothing to cut when idle

        echo = _FakeConnection(speaking=True)
        echo.spoken.append((time.time(), "the entry point is command main in src click core py"))
        s2 = MeetingSession(workroom=_FakeWorkroom(result=_result(sent=[])), connection=echo)
        await s2.on_partial("Bob", "the entry point is command main in src click core py", ts=1.0)
        assert echo.speak.cuts == 0          # Proxy's own echo never self-barges

    asyncio.run(_run())


def test_runtime_ingest_partial_drops_when_no_session() -> None:
    """BUG 3: a partial before the session is wired is DROPPED (not buffered) — a partial can only
    barge in on active speech, which cannot exist before the meeting is live. Never raises."""
    from control_plane.meeting_runtime import MeetingRuntime

    async def _run() -> None:
        rt = MeetingRuntime(meeting_id="m-p")
        await rt.ingest_partial("Bob", "some words while unwired", ts=1.0)  # no session yet
        assert rt.pending_lines == []        # a partial is never buffered

    asyncio.run(_run())


# ── BUG 5: the turns queue must never block ingest/drain; barge-in cuts mid-turn ──


def test_barge_in_cut_fires_while_a_turn_is_in_flight() -> None:
    """BUG 5: 'stop talking' must bypass the queue. The barge-in cut runs SYNCHRONOUSLY in on_line
    (before any wake is spawned), so it fires even while a prior wake's run_ask is still in flight —
    it never queues behind the running turn."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection(speaking=True)
        turn_running = asyncio.Event()
        may_finish = asyncio.Event()

        class _SlowWorkroom:
            def __init__(self) -> None:
                self.asks: list[str] = []
                self.fed: list[str] = []

            async def feed_transcript(self, md: str) -> None:
                self.fed.append(md)

            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                turn_running.set()
                await asyncio.wait_for(may_finish.wait(), timeout=2.0)
                return _result(sent=[])

        wr = _SlowWorkroom()
        session = MeetingSession(workroom=wr, connection=conn)

        # First line wakes Proxy — its run_ask blocks (a long in-flight turn).
        await session.on_line("Bob", "proxy, do the big task", ts=1.0)
        await asyncio.wait_for(turn_running.wait(), timeout=2.0)

        # While that turn is in flight, a human talks over Proxy ("stop talking"). The cut must fire
        # NOW (synchronously in on_line, before any wake is spawned) — not behind the queued turn. The
        # cut is the load-bearing guarantee here: the barge-in reflex bypasses the single-flight queue.
        # ("proxy stop talking" also matches the wake gate, so a wake is spawned too — its begin_turn
        # then lowers the latch again; in production that wake judges silence and speaks nothing.)
        await session.on_line("Bob", "proxy stop talking", ts=2.0)
        assert conn.speak.cuts == 1          # cut fired mid-turn (bypassed the queue)

        may_finish.set()
        await session.drain()

    asyncio.run(_run())


def test_ingest_never_blocks_behind_an_in_flight_turn() -> None:
    """BUG 5 req 1+2: the transcript feed + wake gate for a NEW line run immediately while a prior
    wake is still in flight — on_line returns without awaiting the running turn (the wake is a
    background task). Hearing is continuous; a new wake is at least SEEN at once."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        first_running = asyncio.Event()
        may_finish = asyncio.Event()

        class _SlowWorkroom:
            def __init__(self) -> None:
                self.asks: list[str] = []
                self.fed: list[str] = []

            async def feed_transcript(self, md: str) -> None:
                self.fed.append(md)

            async def run_ask(self, ask: str, *, delta: str = "") -> Any:
                self.asks.append(ask)
                if "first" in ask:
                    first_running.set()
                    await asyncio.wait_for(may_finish.wait(), timeout=2.0)
                return _result(sent=[])

        wr = _SlowWorkroom()
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, the first big task", ts=1.0)
        await asyncio.wait_for(first_running.wait(), timeout=2.0)

        # A NEW line arrives mid-turn. on_line must return promptly (feed + spawn), NOT block on the
        # in-flight first turn.
        await asyncio.wait_for(
            session.on_line("Ann", "someone said something new", ts=2.0), timeout=0.5
        )
        assert len(wr.fed) >= 2              # the new line was fed while the first turn ran

        may_finish.set()
        await session.drain()

    asyncio.run(_run())


def test_pre_wire_lines_buffer_and_flush_in_order_on_wire_session() -> None:
    """THE join-race contract: lines that arrive BEFORE the session is wired (registration
    precedes the ~tens-of-seconds assembly; the liveness utterance itself triggers the
    provision) are buffered and flushed IN ORDER by wire_session — never silently dropped."""
    import asyncio

    from control_plane.meeting_runtime import MeetingRuntime

    class _FakeSession:
        def __init__(self) -> None:
            self.lines: list[tuple[str, str, float, bool]] = []
            self.batches: list[list[tuple[str, str, float, bool]]] = []

        async def on_line(
            self, speaker: str, text: str, *, ts: float = 0.0, is_chat: bool = False
        ) -> None:
            self.lines.append((speaker, text, ts, is_chat))

        async def catch_up(
            self, lines: list[tuple[str, str, float, bool]]
        ) -> None:
            self.batches.append(list(lines))
            self.lines.extend(lines)

    async def _run() -> None:
        rt = MeetingRuntime(meeting_id="m-1")
        # pre-wire: both lines buffer (no session yet), nothing raises
        await rt.ingest_line("Riya", "Hey Proxy, can you hear me?", ts=1.0)
        await rt.ingest_line("Daksh", "second line", ts=2.0, is_chat=True)
        assert len(rt.pending_lines) == 2
        s = _FakeSession()
        await rt.wire_session(s)  # type: ignore[arg-type]
        # ONE catch-up batch (not per-line feeds): both lines arrive together.
        assert s.batches == [
            [
                ("Riya", "Hey Proxy, can you hear me?", 1.0, False),
                ("Daksh", "second line", 2.0, True),
            ]
        ]
        assert rt.pending_lines == []
        # post-wire: lines feed straight through
        await rt.ingest_line("Riya", "third", ts=3.0)
        assert s.lines[-1] == ("Riya", "third", 3.0, False)

    asyncio.run(_run())


def test_speak_delivery_failure_is_an_honest_degrade_not_silent_success() -> None:
    """T5 HONEST DELIVERY (Law 2). Relay mode: the agent intended to SPEAK, the sentence was streamed,
    but the sandbox's relay POST FAILED — the room heard nothing, yet ``result.sent`` is empty and
    ``result.error`` is None (the failure was a skipped ``relay_error`` line) and ``deliver_at`` is set
    (the sentence was flushed). Without the fix the driver reads deliver_at>0 and treats the turn as a
    silent delivered success. With ``delivery_failed=True`` the driver must instead speak ONE honest
    degrade line so a needed answer is never met with silence."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="the answer", sent=[], deliver_at=3.1, ttft=1.0,
                                          delivery_failed=True))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, what's the fix?", ts=1.0)
        await session.drain()

        assert len(conn.sent) == 1 and conn.sent[-1].medium == "say", \
            "a delivery failure speaks ONE honest degrade over the host-side connection"
        low = conn.sent[-1].content.lower()
        assert "trouble" in low or "problem" in low
        # the internal prose is never put in Proxy's mouth
        assert "the answer" not in conn.sent[-1].content

    asyncio.run(_run())


def test_delivered_turn_without_a_failure_is_not_degraded() -> None:
    """No regression: a relay-mode turn that DELIVERED cleanly (delivery_failed False) still opens the
    follow-up window and speaks NO degrade line — the honest-degrade fires only on a real miss."""
    from control_plane.meeting_session import MeetingSession

    async def _run() -> None:
        conn = _FakeConnection()
        wr = _FakeWorkroom(result=_result(text="done", sent=[], deliver_at=2.0, ttft=1.0,
                                          delivery_failed=False))
        session = MeetingSession(workroom=wr, connection=conn)

        await session.on_line("Bob", "proxy, status?", ts=1.0)
        await session.drain()

        assert conn.sent == [], "a clean delivered turn speaks no degrade"
        assert session._follow_up_until > 0.0, "a clean delivered turn opens the follow-up window"

    asyncio.run(_run())
