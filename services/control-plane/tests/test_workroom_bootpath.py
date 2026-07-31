"""The WORKROOM boot-path proof — the reactive spine serves a meeting on the REAL path.

This exercises the actual rewired cutover wiring end-to-end with only the genuine externals
faked at their exact seam boundaries:

  * E2B cloud            -> a ``FakeSandbox`` returned through the real ``e2b_sandbox_class`` seam
  * Cartesia+Recall audio -> a ``FakeSpeakPipe`` returned through the real ``real_speak_sink`` seam
  * Postgres repo row     -> ``repos.meetings.get_repo_by_id`` returns the bound repo's full_name
  * Recall room egress    -> a ``FakeRoom`` (the RecallTransport verbs), never called on the happy
                            path (the agent responds by voice via ``to_meeting`` medium='say')

EVERYTHING ELSE IS THE REAL PRODUCT CODE: ``provisioner._assemble_workroom`` (token gate, repo
resolve, honest-degrade, connection + session assembly), ``provision_workroom`` (real
clone/setup/seed command sequence through the real ``call_external`` retry+telemetry seam), the
real ``Workroom`` methods, the real ``_parse_stream``, the real ``MeetingSession`` (transcript-in
-> wake gate -> run_ask -> respond), the real ``MeetingConnection`` (medium routing to the physical
pipe), and the real ``MeetingRuntimeRegistry.end_meeting`` teardown drain+kill.

The meeting is driven exactly as the webhook feed drives it in production: one
``session.on_line(speaker, text, ts=...)`` per transcript line (``webhooks.py`` ->
``runtime.ingest_line`` -> ``session.on_line``). So a green run here means: a real join
provisions a workroom, streams the transcript into it, wakes native Claude only on an addressed
line, does grounded work, speaks the result back to the room over the meeting connection, and
tears the sandbox down — on the real reactive-workroom boot path.

The one thing this does NOT cover (by design, the founder's call) is a LIVE E2B sandbox + LIVE
Anthropic subscription + LIVE Cartesia/Recall round-trip — that live-vendor smoke is the founder's
to run. Here the vendor edges are faked at their seams; the orchestration is real.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

# The canonical grounded answer a real native-Claude turn would stream back as stream-json:
# two tool_use events (Bash, then Read) then a final result carrying a file:line citation + cost.
_CANNED_STREAM = "\n".join(
    [
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Read"}]}}),
        json.dumps({"type": "result",
                    "result": "The slugify helper lives at packages/lib/slugify.ts:4 — it "
                              "lowercases then strips non-alphanumerics. I read the actual file "
                              "to confirm.",
                    "total_cost_usd": 0.0123}),
    ]
)


class _FakeFiles:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def write(self, path: str, content: str) -> None:
        self._store[path] = content

    async def read(self, path: str) -> str:
        return self._store.get(path, "")


class _FakeCommands:
    def __init__(self, store: dict[str, str], log: list[str]) -> None:
        self._store = store
        self._log = log

    async def run(self, cmd: str, timeout: int | None = None,
                  envs: dict[str, str] | None = None) -> SimpleNamespace:
        self._log.append(cmd)
        # A woken turn shells out to native ``claude`` and redirects its stream-json to
        # /tmp/ask.jsonl; the fake "produces" that file so the real reader+parser run for real.
        if "claude -p" in cmd:
            self._store["/tmp/ask.jsonl"] = _CANNED_STREAM
        return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")


class FakeSandbox:
    """An in-process stand-in for e2b ``AsyncSandbox`` — the true external, faked at its seam."""

    created_kwargs: list[dict] = []

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.cmd_log: list[str] = []
        self.files = _FakeFiles(self._store)
        self.commands = _FakeCommands(self._store, self.cmd_log)
        self.sandbox_id = "sbx-fake-boot"
        self.killed = False

    @classmethod
    async def create(cls, **kwargs: object) -> "FakeSandbox":
        FakeSandbox.created_kwargs.append(dict(kwargs))
        return cls()

    async def kill(self) -> None:
        self.killed = True

    async def set_timeout(self, seconds: int) -> None:  # keep-warm surface (unused here)
        return None


class FakeSpeakPipe:
    """An in-process stand-in for the Cartesia->Recall speak pipe (the audio egress external)."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.flushed = 0
        self.cuts = 0
        self.closed = 0

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def flush(self) -> None:
        self.flushed += 1

    async def cut(self) -> None:
        self.cuts += 1

    async def aclose(self) -> None:
        self.closed += 1


class FakeRoom:
    """The Recall room verbs (RoomSink) — creds host-side; unused on the voice happy path."""

    def __init__(self) -> None:
        self.chats: list[str] = []

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.chats.append(message)

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.chats.append(f"dm:{message}")

    async def mute(self, bot_id: str) -> None:
        return None

    async def unmute(self, bot_id: str) -> None:
        return None


class _FakeAcquire:
    async def __aenter__(self) -> object:
        return SimpleNamespace()  # the conn; the repo lookup is monkeypatched, never touches it

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeDB:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


def test_workroom_serves_a_meeting_on_the_real_bootpath(monkeypatch) -> None:
    FakeSandbox.created_kwargs.clear()

    import in_meeting.speak as speakmod
    import libs.http.src.http.external as ext
    from libs.db import repos

    pipe = FakeSpeakPipe()
    room = FakeRoom()
    # Fake the externals AT THEIR SEAMS (everything downstream is the real product code):
    monkeypatch.setattr(ext, "e2b_sandbox_class", lambda: FakeSandbox)
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)

    async def _fake_repo(conn: object, repo_id: object) -> dict[str, str]:
        return {"id": repo_id, "tenant_id": "t1", "full_name": "calcom/cal.com"}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _fake_repo)

    async def _run() -> None:
        from control_plane import provisioner
        from in_meeting.workroom import MAP_FILE, PRIME_FILE, REPO_DIR, TRANSCRIPT_FILE

        resolved = {"id": "m-boot-1", "repo_id": 42, "pinned_sha": None}
        premeeting_map = "# cal.com pre-meeting map\n- packages/lib/slugify.ts — the slug helper"
        meeting_info = "# Meeting\n\n**Participants:**\n- Alice\n- Bob\n"
        session, workroom, connection, speak_pipe = await provisioner._assemble_workroom(
            resolved, db=FakeDB(), bot_id="bot-xyz", transport=room,
            oauth_token="sk-oauth-test", map_text=premeeting_map, meeting_info=meeting_info,
        )

        # (1) Assembly succeeded through the REAL _assemble_workroom + REAL provision_workroom.
        assert session is not None, "session should assemble with a token + bound repo"
        assert workroom is not None
        assert connection is not None
        assert speak_pipe is pipe
        sandbox = workroom.sandbox
        # provision_workroom built the real setup command for THIS repo (clone into the sandbox):
        setup = "\n".join(sandbox.cmd_log)
        assert "git clone" in setup
        assert "github.com/calcom/cal.com" in setup
        # ...and seeded the orientation files inside the sandbox — INCLUDING the pre-meeting map
        # threaded from repo_maps through _assemble_workroom (MAP-LOAD: the pre-meeting system's
        # contribution reaches the workroom, so Claude opens oriented):
        assert PRIME_FILE in sandbox._store
        assert TRANSCRIPT_FILE in sandbox._store
        assert sandbox._store[MAP_FILE] == premeeting_map
        # ...and MEETING_INFO.md (who's in the room) seeded by _assemble_workroom (SPEC §2/§8):
        assert sandbox._store[f"{REPO_DIR}/MEETING_INFO.md"] == meeting_info
        # the sandbox was provisioned with a real timeout budget (the create seam ran):
        assert FakeSandbox.created_kwargs and "timeout" in FakeSandbox.created_kwargs[0]

        # (2) Drive the meeting EXACTLY as the webhook feed does — one on_line per transcript line.
        await session.on_line("Alice", "Let's kick off the release sync", ts=1.0)
        # a non-addressed line must NOT wake the (expensive) workroom:
        assert not session.results
        await session.on_line("Bob", "proxy, where's the slugify helper?", ts=2.0)
        await session.drain()  # await the background wake (monitor-while-working)

        # (3) The addressed line woke native Claude, which did grounded work + spoke it back.
        assert len(session.results) == 1, "exactly one wake, on the addressed line"
        res = session.results[0]
        assert "slugify.ts:4" in res.text          # grounded file:line, from the REAL parse
        assert res.tools == ["Bash", "Read"]        # native tools, ordered, from the REAL parse
        assert res.cost_usd > 0.0                    # cost surfaced from the stream
        # presented to the room via the REAL MeetingConnection.to_meeting -> the (faked) pipe:
        assert any("slugify.ts:4" in s for s in pipe.said)
        assert pipe.flushed >= 1
        # the send was recorded on the connection as a voice send (the host-observed record):
        assert connection.sent and connection.sent[-1].medium == "say"
        assert connection.sent[-1].ok is True
        # the live transcript was materialized into the sandbox BEFORE the wake read it:
        notes = sandbox._store[TRANSCRIPT_FILE]
        assert "slugify" in notes
        assert "Alice" in notes and "Bob" in notes

    asyncio.run(_run())


def test_end_meeting_drains_and_tears_down_on_the_real_registry(monkeypatch) -> None:
    """Meeting end on the REAL registry: drain in-flight turns + flush the pipe + kill the
    sandbox + drop the runtime (the ordered teardown, all through the real functions)."""
    import in_meeting.speak as speakmod
    import libs.http.src.http.external as ext
    from libs.db import repos

    pipe = FakeSpeakPipe()
    room = FakeRoom()
    monkeypatch.setattr(ext, "e2b_sandbox_class", lambda: FakeSandbox)
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)

    async def _fake_repo(conn: object, repo_id: object) -> dict[str, str]:
        return {"id": repo_id, "tenant_id": "t1", "full_name": "calcom/cal.com"}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _fake_repo)

    async def _run() -> None:
        from control_plane import provisioner
        from control_plane.meeting_runtime import MeetingRuntime, MeetingRuntimeRegistry

        resolved = {"id": "m-boot-2", "repo_id": 42, "pinned_sha": None}
        session, workroom, connection, speak_pipe = await provisioner._assemble_workroom(
            resolved, db=FakeDB(), bot_id="bot-xyz", transport=room,
            oauth_token="sk-oauth-test",
        )
        registry = MeetingRuntimeRegistry(FakeDB())
        runtime = MeetingRuntime(
            meeting_id="m-boot-2",
            session=session,
            workroom=workroom,
            connection=connection,
            speak_pipe=speak_pipe,
        )
        registry.register(runtime)
        assert registry.get("m-boot-2") is not None

        await registry.end_meeting("m-boot-2", reason="call_ended")

        assert workroom.sandbox.killed is True          # the sandbox was killed
        assert pipe.closed >= 1                           # the speak pipe was flushed + closed
        assert registry.get("m-boot-2") is None           # the runtime was dropped (idempotent)
        # a second end is a safe no-op:
        await registry.end_meeting("m-boot-2")

    asyncio.run(_run())


def test_assemble_workroom_degrades_honestly_without_a_token(monkeypatch) -> None:
    """No subscription token -> the meeting still boots, just without a workroom (Law-3 honest
    degrade, never a crash). Returns (None, None, None, speak_pipe) — the caller shape."""
    import in_meeting.speak as speakmod

    pipe = FakeSpeakPipe()
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    async def _run() -> None:
        from control_plane import provisioner

        resolved = {"id": "m-boot-3", "repo_id": 42, "pinned_sha": None}
        session, workroom, connection, speak_pipe = await provisioner._assemble_workroom(
            resolved, db=FakeDB(), bot_id="bot-xyz", transport=SimpleNamespace(),
            oauth_token=None,
        )
        assert session is None
        assert workroom is None
        assert connection is None
        assert speak_pipe is pipe  # the meeting still has a voice channel; just no workroom brain

    asyncio.run(_run())


def test_wake_gate_voice_vs_chat_and_self() -> None:
    """The cheap word-bounded wake gate (the ONLY situation→action logic): voice wakes on a
    spoken 'proxy', chat requires '@proxy' (a plain chat mention of 'proxy' does NOT wake),
    and Proxy's own lines never wake it (no self-wake)."""
    from control_plane.meeting_session import is_addressed

    # voice: a spoken address wakes; a bare word still counts (a bounded confirm refines it).
    assert is_addressed("Bob", "proxy, check the DST bug") is not None
    assert is_addressed("Bob", "nothing to see here") is None
    # chat: only '@proxy' wakes — a plain mention of the word 'proxy' in chat does NOT.
    assert is_addressed("Bob", "@proxy where's the helper?", is_chat=True) is not None
    assert is_addressed("Bob", "the proxy config broke again", is_chat=True) is None
    # Proxy's own line never wakes it (self-wake guard), voice or chat.
    assert is_addressed("Proxy", "proxy here is the answer") is None
    assert is_addressed("Proxy", "@proxy self", is_chat=True) is None
