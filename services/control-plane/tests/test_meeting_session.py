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
            sent: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    """A stand-in WorkroomResult carrying the fields _handle reads: text, error, and the agent's
    OWN recorded ``to_meeting`` intents (``sent``). ``sent`` defaults to [] (no recorded intents)."""
    return SimpleNamespace(text=text, error=error, sent=list(sent or []))


def _say(content: str) -> list[dict[str, Any]]:
    """One recorded 'say' intent — the common no-relay channel choice, so a scripted turn's result
    replays as a spoken line over the connection (the agent's OWN choice, not our prose)."""
    return [{"content": content, "medium": "say", "to": ""}]


class _FakeConnection:
    """Records what actually reached the room, mirroring MeetingConnection.sent / to_meeting —
    including the ``spoken`` log (voice lines only) the reactive loop reads for self-echo
    suppression, recorded exactly as the real connection's ``_record_spoken`` does."""

    def __init__(self) -> None:
        self.sent: list[SimpleNamespace] = []
        self.spoken: list[tuple[float, str]] = []

    async def to_meeting(self, content: str, medium: str = "say", to: str | None = None) -> Any:
        rec = SimpleNamespace(medium=medium, ok=True, detail="", content=content)
        self.sent.append(rec)
        if medium in ("say", "speak", "voice") and content.strip():
            self.spoken.append((time.time(), content))
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

            async def run_ask(self, ask: str) -> Any:
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
