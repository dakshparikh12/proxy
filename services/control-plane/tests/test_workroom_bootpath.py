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
    def __init__(self, store: dict[str, str], log: list[str],
                 env_log: list[dict[str, str]]) -> None:
        self._store = store
        self._log = log
        self._env_log = env_log

    async def run(self, cmd: str, timeout: int | None = None,
                  envs: dict[str, str] | None = None) -> SimpleNamespace:
        self._log.append(cmd)
        self._env_log.append(dict(envs or {}))
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
        self.env_log: list[dict[str, str]] = []
        self.files = _FakeFiles(self._store)
        self.commands = _FakeCommands(self._store, self.cmd_log, self.env_log)
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
    # A deployment origin ⇒ the provisioner mints a reachable relay URL for the in-sandbox MCP
    # server (the live path; unset it degrades to no relay + result-text speak, tested elsewhere).
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")

    async def _run() -> None:
        from control_plane import provisioner
        from in_meeting.workroom import (
            MAP_FILE,
            MCP_CONFIG_FILE,
            MCP_SERVER_FILE,
            PRIME_FILE,
            REPO_DIR,
            TRANSCRIPT_FILE,
        )

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

        # (1b) THE DYNAMIC MEETING INTERFACE IS WIRED (SPEC §4/§5): provision installed the pinned
        # mcp SDK, wrote the in-sandbox MCP server + the .mcp.json that registers it with native
        # ``claude`` (stdio) — so a woken turn can call ``to_meeting`` and reach the room live.
        import json as _json

        assert "pip3 install" in setup and "mcp==1.28.1" in setup   # the MCP SDK was installed
        mcp_src = sandbox._store[MCP_SERVER_FILE]                    # the server source landed
        assert "def to_meeting(" in mcp_src and "PROXY_MEETING_RELAY" in mcp_src
        mcp_cfg = _json.loads(sandbox._store[MCP_CONFIG_FILE])           # a valid stdio .mcp.json
        assert mcp_cfg["mcpServers"]["meeting"]["command"] == "python3"
        assert mcp_cfg["mcpServers"]["meeting"]["args"] == [MCP_SERVER_FILE]
        # the provisioner minted a per-meeting relay bearer + a reachable relay URL for the sandbox:
        assert workroom.relay_token, "a per-meeting relay bearer was minted"
        assert workroom.relay_url == "https://proxy.example.com/meetings/m-boot-1/relay"

        # (2) Drive the meeting EXACTLY as the webhook feed does — one on_line per transcript line.
        await session.on_line("Alice", "Let's kick off the release sync", ts=1.0)
        # a non-addressed line must NOT wake the (expensive) workroom:
        assert not session.results
        await session.on_line("Bob", "proxy, where's the slugify helper?", ts=2.0)
        await session.drain()  # await the background wake (monitor-while-working)

        # (3) The addressed line woke native Claude, which did grounded work.
        assert len(session.results) == 1, "exactly one wake, on the addressed line"
        res = session.results[0]
        assert "slugify.ts:4" in res.text          # grounded file:line, from the REAL parse
        assert res.tools == ["Bash", "Read"]        # native tools, ordered, from the REAL parse
        assert res.cost_usd > 0.0                    # cost surfaced from the stream

        # (3b) run_ask launched native ``claude`` WITH the meeting MCP server and handed it the
        # relay wiring, so a live turn's ``to_meeting`` calls can reach the host (SPEC §4/§5):
        ask_cmd = next(c for c in sandbox.cmd_log if "claude -p" in c)
        assert f"--mcp-config {MCP_CONFIG_FILE}" in ask_cmd   # the MCP server is loaded for the turn
        ask_envs = sandbox.env_log[sandbox.cmd_log.index(ask_cmd)]
        assert ask_envs["PROXY_MEETING_RELAY"] == workroom.relay_url    # relay endpoint
        assert ask_envs["PROXY_MEETING_TOKEN"] == workroom.relay_token  # per-meeting bearer
        assert ask_envs["PROXY_MEETING_OUT"] == "/tmp/to_meeting.jsonl"
        assert ask_envs["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth-test"   # subscription auth

        # (3c) FALLBACK path: the FAKE claude only emits the canned stream (it does not actually
        # invoke the in-sandbox MCP server), so the agent made ZERO live to_meeting calls this turn.
        # The session honest-degrades by speaking the result text through the REAL connection:
        assert any("slugify.ts:4" in s for s in pipe.said)
        assert pipe.flushed >= 1
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


def _relay_runtime(bot_id: str = "bot-relay") -> tuple[Any, Any, Any]:
    """Build a REAL MeetingRuntime whose connection carries a per-meeting relay bearer, wired the
    way the provisioner does — used to prove a relayed ``to_meeting`` lands on the connection."""
    from control_plane.meeting_runtime import MeetingRuntime, MeetingRuntimeRegistry
    from in_meeting.meeting_connection import MeetingConnection

    room = FakeRoom()

    class _Speak:
        def __init__(self) -> None:
            self.said: list[str] = []

        async def say(self, text: str) -> None:
            self.said.append(text)

        async def cut(self) -> None:
            return None

    speak = _Speak()
    connection = MeetingConnection(speak=speak, room=room, bot_id=bot_id)
    # The workroom holds the per-meeting relay bearer the relay route authenticates against
    # (the provisioner stashes it there); a light stand-in is enough for the route lookup.
    workroom = SimpleNamespace(relay_token="secret-relay-token")
    runtime = MeetingRuntime(meeting_id="m-relay", connection=connection, workroom=workroom)
    registry = MeetingRuntimeRegistry(FakeDB())
    registry.register(runtime)
    return registry, connection, speak


def test_relay_route_lands_a_to_meeting_call_on_the_connection() -> None:
    """SPEC §4/§5: a ``to_meeting`` call the in-sandbox MCP server POSTs to the host relay route
    authenticates the per-meeting bearer and lands on the REAL ``MeetingConnection`` — the sandbox→
    host round-trip, proven with the vendor edges faked. Also proves the never-throw scoping: a
    missing/wrong bearer is 401, an unknown meeting is 404, all without a crash."""
    from fastapi.testclient import TestClient

    from control_plane.app import create_app

    registry, connection, speak = _relay_runtime()
    app = create_app()
    app.state.meeting_runtimes = registry
    client = TestClient(app)

    auth = {"Authorization": "Bearer secret-relay-token"}

    # (a) a said intent lands on the connection -> the (faked) speak pipe:
    r = client.post("/meetings/m-relay/relay",
                    json={"content": "The DST bug is in tz.ts:88", "medium": "say", "to": ""},
                    headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert speak.said == ["The DST bug is in tz.ts:88"]
    assert connection.sent[-1].medium == "say"

    # (b) a chat intent routes to the Recall room verb (the agent's DYNAMIC medium choice):
    r = client.post("/meetings/m-relay/relay",
                    json={"content": "posting a summary", "medium": "chat"}, headers=auth)
    assert r.status_code == 200 and r.json()["medium"] == "chat"
    assert connection.room.chats[-1] == "posting a summary"

    # (c) a missing/wrong bearer is rejected 401 — the sandbox→host trust plane, fail-closed:
    r = client.post("/meetings/m-relay/relay", json={"content": "x"})
    assert r.status_code == 401
    r = client.post("/meetings/m-relay/relay", json={"content": "x"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401

    # (d) an unknown meeting is an honest 404, never a crash:
    r = client.post("/meetings/nope/relay", json={"content": "x"},
                    headers={"Authorization": "Bearer secret-relay-token"})
    assert r.status_code == 401  # no runtime ⇒ no expected token ⇒ fail-closed before the 404


def test_session_stays_quiet_when_the_agent_acted_live_via_relay() -> None:
    """SPEC §5: when the agent reached the room DURING the turn (a to_meeting relay call recorded
    on the connection), the session must NOT also speak the result text — the agent already
    communicated. Only a turn with ZERO live sends falls back to speaking the result."""
    from control_plane.meeting_session import MeetingSession
    from in_meeting.meeting_connection import MeetingConnection

    room = FakeRoom()
    pipe = FakeSpeakPipe()

    class _SpeakSink:
        async def say(self, text: str) -> None:
            pipe.said.append(text)

        async def cut(self) -> None:
            return None

    connection = MeetingConnection(speak=_SpeakSink(), room=room, bot_id="bot-x")

    class _LiveWorkroom:
        """A workroom whose turn reaches the room live (as the in-sandbox MCP relay would)."""

        def __init__(self, conn: Any) -> None:
            self._conn = conn

        async def feed_transcript(self, md: str) -> None:
            return None

        async def run_ask(self, ask: str) -> Any:
            # The agent chose chat live during the turn (what the relay would have landed):
            await self._conn.to_meeting("here's the answer", medium="chat")
            return SimpleNamespace(text="here's the answer", error=None)

    async def _run() -> None:
        session = MeetingSession(workroom=_LiveWorkroom(connection), connection=connection)
        await session.on_line("Bob", "proxy, what's the answer?", ts=1.0)
        await session.drain()
        # the agent acted live (one chat send); the session added NO second 'say' fallback:
        assert [s.medium for s in connection.sent] == ["chat"]
        assert pipe.said == []  # stayed quiet — did not re-speak the result text

    asyncio.run(_run())
