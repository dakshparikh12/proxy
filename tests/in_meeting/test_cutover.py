"""CUTOVER — the NEW in-meeting engine owns the REAL production boot path.

The swap node: ``control_plane.provisioner`` assembles the NEW engine
(``in_meeting.runtime.assemble_engine``) instead of the old live brain, the
webhook drain adapts transcript + chat events onto ``Engine.feed_transcript`` /
``Engine.feed_chat``, the meeting-end lifecycle drains the engine + closes the
speak pipe + kills the sandbox, and the control-plane app serves the
Output-Media surface. Offline + deterministic: every vendor seam is a fake
(no Recall / Cartesia / E2B / Anthropic call can fire) and the db is an
in-memory stand-in — the PG-gated e2e provisioner tests keep proving the
claim/loop physics on live Postgres.

The six ACs (task CUTOVER):
  1. boot assembly — a meetings row + fakes → the Engine is constructed with
     code+meeting+sandbox tools and the map, and the clone_path is TENANT-ROOTED
     (the exact ``premeeting.paths.tenant_repo_dir`` derivation);
  2. transcript dispatch — a drained transcript event feeds the engine the
     exact ``TranscriptLine`` (and the notes plane keeps its feed);
  3. chat dispatch — Recall's REAL chat event (``participant_events.chat_message``,
     confirmed against docs.recall.ai real-time event payloads) feeds the engine
     the exact ``ChatLine``;
  4. sandbox degrade — a provision fault boots the meeting WITHOUT sandbox
     tools, honestly logged, never a dead meeting;
  5. meeting end — the end signal drains the engine, closes the speak pipe,
     kills the sandbox, and completes the operation_runs row;
  6. router mounted — the control-plane app serves GET /output-media/{id}.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting.engine import CODE_TOOLS
from in_meeting.meeting_control import MEETING_TOOLS
from in_meeting.notes import TranscriptLine
from in_meeting.sandbox import SANDBOX_TIMEOUT_S, SANDBOX_TOOLS
from in_meeting.trigger import ChatLine

_ANSWER = "on it, the retry logic is in client.py:42"
_ASK = TranscriptLine(text="Proxy, where is the retry logic?", speaker="Devon", timestamp=20.0, end_of_turn=True)


# ── shared fakes (every vendor seam) ──────────────────────────────────────────


def _happy_turn() -> list[AgentChunk]:
    return [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "s-1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="TEXT", text=_ANSWER, metadata={"msg_id": "m-1"}),
        AgentChunk(type="RESULT", text=_ANSWER, metadata={"session_id": "s-1", "total_cost_usd": 0.01}),
    ]


class FakeProvider:
    """A scripted ``agentkit.Provider``: records every (prompt, query) call."""

    def __init__(self, turns: Sequence[Sequence[AgentChunk]] | None = None) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._turns: list[list[AgentChunk]] = [list(t) for t in (turns or [_happy_turn()])]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        script = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        for chunk in script:
            yield chunk


class FakeTransport:
    """The MeetingControlTransport verbs as inert recorders."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def mute(self, bot_id: str) -> None:
        self.calls.append(f"mute:{bot_id}")

    async def unmute(self, bot_id: str) -> None:
        self.calls.append(f"unmute:{bot_id}")

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.calls.append(f"post_chat:{bot_id}")

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.calls.append(f"send_dm:{bot_id}")


class FakeSandboxHandle:
    """The provisioned E2B handle shape (commands/files/kill) as a recorder."""

    def __init__(self) -> None:
        self.killed = False

    @property
    def commands(self) -> Any:
        return None

    @property
    def files(self) -> Any:
        return None

    async def kill(self) -> None:
        self.killed = True


class FakeSandboxBackend:
    """The injectable ``provision_sandbox`` backend: records the create kwargs."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.create_kwargs: list[dict[str, Any]] = []
        self.handle = FakeSandboxHandle()

    async def __call__(self, **kwargs: Any) -> FakeSandboxHandle:
        self.create_kwargs.append(kwargs)
        if self.fail:
            raise RuntimeError("e2b provision blew up (scripted)")
        return self.handle


class FakeSpeakPipe:
    """The speak seam: an async ``say`` (SpeakSink shape) + a recorded aclose."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.closed = False

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def aclose(self) -> None:
        self.closed = True


async def _confirm_every_hit(line: str) -> bool:
    return True


class _LoaderRecorder:
    """Stands in for ``in_meeting.runtime.load_meeting_map`` (recorded pinned key)."""

    def __init__(self, map_text: str | None) -> None:
        self.map_text = map_text
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, conn: Any, tenant_id: str, repo: str, pinned_sha: str) -> str | None:
        self.calls.append({"tenant_id": tenant_id, "repo": repo, "pinned_sha": pinned_sha})
        return self.map_text


class FakeConn:
    """A minimal asyncpg-conn stand-in: routes fetchrow by table, records execs."""

    def __init__(
        self,
        *,
        meeting_row: dict[str, Any] | None = None,
        repo_row: dict[str, Any] | None = None,
        pending_webhooks: list[dict[str, Any]] | None = None,
    ) -> None:
        self.meeting_row = meeting_row
        self.repo_row = repo_row
        self.pending_webhooks = list(pending_webhooks or [])
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM meetings" in sql:
            return self.meeting_row
        if "FROM repos" in sql:
            return self.repo_row
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM webhook_events" in sql:
            return list(self.pending_webhooks)
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "UPDATE 1"


class FakeDb:
    """A ``db.acquire()``-shaped stand-in handing out ONE shared FakeConn."""

    instance_id = "cutover-test-instance"

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> Any:
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Ctx()


_MAP = "# Repo Map\n- retries live in libs/http/client.py"


def _resolved_row(tenant: str = "tenant-1") -> dict[str, Any]:
    return {
        "id": "m-1",
        "tenant_id": tenant,
        "repo_id": "r-1",
        "pinned_sha": "abc123",
        "recall_bot_id": "bot-7",
        "meeting_url": "https://meet.example/x",
    }


def _repo_row(tenant: str = "tenant-1") -> dict[str, Any]:
    return {"id": "r-1", "tenant_id": tenant, "full_name": "acme/widget", "default_branch": "main"}


# ── AC1: boot assembly — engine constructed with the full access, tenant-rooted ──


@pytest.mark.asyncio
async def test_boot_assembly_constructs_engine_with_full_access_and_tenant_rooted_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC1 — the provisioner's new core assembles the Engine off the meetings row:
    the captured provider query carries CODE+MEETING+SANDBOX tools and the map,
    the loader got the meeting's exact pinned (tenant, repo, sha) key, the model
    is the ORCHESTRATOR seat, and the clone_path is the TENANT-ROOTED
    ``premeeting.paths.tenant_repo_dir(...)/checkout`` derivation."""
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime
    from premeeting.paths import tenant_repo_dir

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setenv("PROXY_MODEL_ORCHESTRATOR", "claude-orch-test")

    # The clone exists ONLY at the tenant-rooted work-tree — the code toolbelt
    # mounting at all proves the tenant_repo_dir derivation structurally.
    clone = tenant_repo_dir("tenant-1", "widget") / "checkout"
    clone.mkdir(parents=True)
    (clone / "client.py").write_text("def retry():\n    return 42\n", encoding="utf-8")

    loader = _LoaderRecorder(_MAP)
    monkeypatch.setattr(im_runtime, "load_meeting_map", loader)

    provider = FakeProvider()
    backend = FakeSandboxBackend()
    pipe = FakeSpeakPipe()
    db = FakeDb(FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row()))

    engine, speak_pipe, sandbox = await prov._assemble_engine(
        _resolved_row(),
        db=db,
        bot_id="bot-7",
        provider=provider,
        transport=FakeTransport(),
        speak=pipe,
        disambiguate=_confirm_every_hit,
        sandbox_backend=backend,
    )

    # The caller's isolation duty: the clone path is the exact tenant-rooted derivation.
    assert prov._engine_clone_path("tenant-1", "widget") == tenant_repo_dir("tenant-1", "widget") / "checkout"

    # The map was loaded for the meeting's exact pinned key — never "latest".
    assert loader.calls == [{"tenant_id": "tenant-1", "repo": "widget", "pinned_sha": "abc123"}]

    # The sandbox was provisioned warm-at-join with the curated create kwargs.
    assert sandbox is backend.handle
    assert backend.create_kwargs == [
        {
            "envs": {},
            "metadata": {"meeting_id": "m-1"},
            "allow_internet_access": False,
            "timeout": SANDBOX_TIMEOUT_S,
        }
    ]
    assert speak_pipe is pipe

    # Drive one addressed wake: the captured query carries the FULL composed access.
    await engine.feed_transcript(_ASK)
    await engine.drain()
    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.model == "claude-orch-test"  # the ORCHESTRATOR seat, env-overridable
    assert query.allowed_tools == CODE_TOOLS + MEETING_TOOLS + SANDBOX_TOOLS
    assert query.mcp_servers is not None
    assert set(query.mcp_servers) == {"code_intel", "meeting", "sandbox"}
    assert _MAP in query.system_prompt
    assert pipe.said == [_ANSWER]


# ── AC4: sandbox degrade — provision fault boots WITHOUT sandbox tools ────────


@pytest.mark.asyncio
async def test_sandbox_provision_failure_degrades_honestly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AC4 — ``provision_sandbox`` raising must NOT kill the meeting: the engine
    still assembles (no sandbox server, no sandbox tool names) and the fault is
    logged honestly."""
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    loader = _LoaderRecorder(None)
    monkeypatch.setattr(im_runtime, "load_meeting_map", loader)

    provider = FakeProvider()
    pipe = FakeSpeakPipe()
    db = FakeDb(FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row()))

    with caplog.at_level("WARNING"):
        engine, _, sandbox = await prov._assemble_engine(
            _resolved_row(),
            db=db,
            bot_id="bot-7",
            provider=provider,
            transport=FakeTransport(),
            speak=pipe,
            disambiguate=_confirm_every_hit,
            sandbox_backend=FakeSandboxBackend(fail=True),
            model="claude-orch-test",
        )

    assert sandbox is None, "a failed provision must degrade to sandbox=None, not raise"
    assert any("sandbox" in rec.message.lower() for rec in caplog.records), (
        "the sandbox provision fault must be logged honestly"
    )

    # The meeting still runs a turn — with NO sandbox names advertised (no clone → meeting only).
    await engine.feed_transcript(_ASK)
    await engine.drain()
    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.allowed_tools == MEETING_TOOLS
    assert query.mcp_servers is not None and set(query.mcp_servers) == {"meeting"}
    assert pipe.said == [_ANSWER]


# ── AC2 + AC3: the webhook drain feeds the engine ─────────────────────────────


class FakeEngine:
    """Captures exactly what the drain dispatch feeds the engine."""

    def __init__(self) -> None:
        self.transcripts: list[TranscriptLine] = []
        self.chats: list[ChatLine] = []

    async def feed_transcript(self, line: TranscriptLine) -> None:
        self.transcripts.append(line)

    async def feed_chat(self, msg: ChatLine) -> None:
        self.chats.append(msg)


class FakeRuntime:
    """The registry entry the dispatch reaches: carries the engine + notes ingest."""

    def __init__(self, engine: FakeEngine | None) -> None:
        self.engine = engine
        self.ingested: list[dict[str, Any]] = []

    async def ingest_transcript(self, body: dict[str, Any]) -> None:
        self.ingested.append(body)


class FakeRegistry:
    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime

    def get(self, meeting_id: str) -> FakeRuntime | None:
        return self._runtime


@pytest.mark.asyncio
async def test_transcript_event_feeds_engine_the_exact_line() -> None:
    """AC2 — a drained transcript event reaches ``engine.feed_transcript`` as the
    exact TranscriptLine adaptation of the wire body (and the notes plane's
    carrier ingest keeps its feed — the durable ledger is not starved)."""
    from control_plane.webhooks import _dispatch_meeting_event

    engine = FakeEngine()
    runtime = FakeRuntime(engine)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))

    payload = {
        "event": "transcript.data",
        "data": {
            "bot_id": "bot-7",
            "words": "Proxy, ping?",
            "speaker": "Sam",
            "timestamp": 12.5,
            "end_of_turn": True,
        },
    }
    await _dispatch_meeting_event(payload, db=db, registry=FakeRegistry(runtime))

    assert engine.transcripts == [
        TranscriptLine(text="Proxy, ping?", speaker="Sam", timestamp=12.5, end_of_turn=True)
    ]
    assert runtime.ingested and runtime.ingested[0]["words"] == "Proxy, ping?", (
        "the notes-plane carrier ingest must keep receiving the wire body"
    )


@pytest.mark.asyncio
async def test_partial_transcript_is_not_fed_to_the_engine() -> None:
    """AC2 (dedupe) — a partial (interim) transcript event must NOT feed the
    engine: only finals reach the notes/trigger, or one spoken ask would wake
    Proxy twice (partial + final carry the same words)."""
    from control_plane.webhooks import _dispatch_meeting_event

    engine = FakeEngine()
    runtime = FakeRuntime(engine)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))

    payload = {
        "event": "transcript.partial_data",
        "data": {"bot_id": "bot-7", "words": "Proxy, pi", "speaker": "Sam", "timestamp": 12.0},
    }
    await _dispatch_meeting_event(payload, db=db, registry=FakeRegistry(runtime))

    assert engine.transcripts == [], "a partial must not reach the engine (double-wake)"
    assert runtime.ingested and runtime.ingested[0]["words"] == "Proxy, pi", (
        "the partial must STILL reach the notes-plane carrier ingest — its coalescer "
        "owns partial/final semantics; only the ENGINE feed is finals-gated"
    )


@pytest.mark.asyncio
async def test_chat_event_feeds_engine_the_exact_chatline() -> None:
    """AC3 — Recall's REAL chat event name (``participant_events.chat_message``,
    docs.recall.ai real-time event payloads) routes the documented payload
    nesting (data.data.participant.name + data.data.data.text) onto
    ``engine.feed_chat`` as the exact ChatLine."""
    from control_plane.webhooks import _CHAT_EVENTS, _dispatch_meeting_event

    assert "participant_events.chat_message" in _CHAT_EVENTS

    engine = FakeEngine()
    runtime = FakeRuntime(engine)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))

    payload = {
        "event": "participant_events.chat_message",
        "data": {
            "data": {
                "participant": {"id": 7, "name": "Priya", "is_host": False},
                "timestamp": {"absolute": "2026-07-29T00:00:00Z", "relative": 4.2},
                "data": {"text": "@proxy summarize the decision", "to": "everyone"},
            },
            "bot": {"id": "bot-7"},
        },
    }
    await _dispatch_meeting_event(payload, db=db, registry=FakeRegistry(runtime))

    assert engine.chats == [ChatLine(sender="Priya", message="@proxy summarize the decision")]


@pytest.mark.asyncio
async def test_chat_event_before_boot_is_a_safe_noop() -> None:
    """AC3 (fail closed) — a chat event with no booted engine never raises."""
    from control_plane.webhooks import _dispatch_meeting_event

    runtime = FakeRuntime(engine=None)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))
    payload = {
        "event": "participant_events.chat_message",
        "data": {"data": {"participant": {"name": "P"}, "data": {"text": "@proxy hi"}}, "bot": {"id": "bot-7"}},
    }
    await _dispatch_meeting_event(payload, db=db, registry=FakeRegistry(runtime))  # must not raise


class RaisingEngine(FakeEngine):
    """An engine whose designed-never-raise feed path ESCAPES (scripted raise)."""

    async def feed_transcript(self, line: TranscriptLine) -> None:
        raise RuntimeError("transcript feed escaped (scripted)")

    async def feed_chat(self, msg: ChatLine) -> None:
        raise RuntimeError("chat feed escaped (scripted)")


@pytest.mark.asyncio
async def test_engine_feed_escape_never_poisons_the_drain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC2/AC3 (poison-row defense) — the Engine's feed path is designed
    never-raise, but an ESCAPE must not leave the webhook row unprocessed: the
    drain logs the fault, still marks BOTH rows processed (never a poison row),
    and the notes-plane carrier ingest still received the transcript body."""
    from control_plane.webhooks import drain_pending_webhooks

    engine = RaisingEngine()
    runtime = FakeRuntime(engine)
    transcript_event = {
        "event": "transcript.data",
        "data": {
            "bot_id": "bot-7",
            "words": "Proxy, ping?",
            "speaker": "Sam",
            "timestamp": 12.5,
            "end_of_turn": True,
        },
    }
    chat_event = {
        "event": "participant_events.chat_message",
        "data": {
            "data": {"participant": {"name": "Priya"}, "data": {"text": "@proxy hi"}},
            "bot": {"id": "bot-7"},
        },
    }
    conn = FakeConn(
        meeting_row=_resolved_row(),
        pending_webhooks=[
            {"id": "wh-1", "payload": transcript_event},
            {"id": "wh-2", "payload": chat_event},
        ],
    )
    db = FakeDb(conn)

    with caplog.at_level("ERROR"):
        drained = await drain_pending_webhooks(db, registry=FakeRegistry(runtime))

    assert drained == 2, "the drain must complete past a raising engine feed"
    processed = [sql for sql, _ in conn.executed if "processed" in sql]
    assert len(processed) == 2, "BOTH rows must still be marked processed (never a poison row)"
    assert runtime.ingested and runtime.ingested[0]["words"] == "Proxy, ping?", (
        "the notes-plane ingest must still receive the body past an engine-feed escape"
    )
    assert sum("feed" in rec.getMessage() for rec in caplog.records) >= 2, (
        "each engine-feed escape must be logged for a human (never silent)"
    )


# ── AC5: meeting end — engine drained, pipe closed, sandbox killed, row done ──


class _NullScribe:
    async def aclose(self) -> None:
        return None

    async def wait(self) -> None:
        return None


@pytest.mark.asyncio
async def test_meeting_end_drains_engine_kills_sandbox_completes_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC5 — the end signal ends the launched meeting: the engine is drained,
    the speak pipe closed, the sandbox killed, and the operation_runs row is
    completed (the existing fencing/close machinery intact)."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setattr(mr, "start_meeting_scribe", lambda *a, **k: _NullScribe())
    monkeypatch.setattr(im_runtime, "load_meeting_map", _LoaderRecorder(None))

    async def _fake_claim(db: Any, meeting_id: str, op: str, *, created_by: Any = None) -> str:
        return "run-1"

    monkeypatch.setattr(prov, "claim_meeting", _fake_claim)

    conn = FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row())
    db = FakeDb(conn)
    registry = mr.MeetingRuntimeRegistry(db)
    backend = FakeSandboxBackend()
    pipe = FakeSpeakPipe()

    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-7"}}

    task = asyncio.ensure_future(
        prov.run_meeting_until_end(
            payload,
            db=db,
            registry=registry,
            timeout_s=5.0,
            provider=FakeProvider(),
            transport=FakeTransport(),
            speak=pipe,
            disambiguate=_confirm_every_hit,
            sandbox_backend=backend,
            model="claude-orch-test",
        )
    )

    # Wait for the boot, then signal the explicit meeting end on the ONE carrier.
    runtime = None
    for _ in range(300):
        runtime = registry.get("m-1")
        if runtime is not None and getattr(runtime, "engine", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert runtime is not None and runtime.engine is not None, "the boot did not assemble the engine"
    assert runtime.speak_pipe is pipe and runtime.engine_sandbox is backend.handle

    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    outcome = await asyncio.wait_for(task, timeout=5.0)

    assert outcome.ran_to_end is True
    assert pipe.closed is True, "the speak pipe was not closed at meeting end"
    assert backend.handle.killed is True, "the sandbox was not killed at meeting end"
    assert registry.get("m-1") is None, "the runtime was not dropped at meeting end"
    assert any("completed" in sql for sql, _ in conn.executed), (
        "the operation_runs row was not completed at meeting end"
    )


class HangingKillSandboxHandle(FakeSandboxHandle):
    """A sandbox handle whose kill HANGS — never returns, never raises.

    ``call_external`` retries on RAISED transport errors but has no wall-clock
    bound of its own, so a hang (a wedged E2B edge) is the exact failure the
    teardown bound must cover."""

    def __init__(self) -> None:
        super().__init__()
        self.kill_started = asyncio.Event()

    async def kill(self) -> None:
        self.kill_started.set()
        await asyncio.Event().wait()  # hangs forever (until cancelled by the bound)


@pytest.mark.asyncio
async def test_hanging_sandbox_kill_is_bounded_and_row_still_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC5 (bounded teardown) — a sandbox kill that HANGS (never returns, never
    raises) must not wedge meeting end: every teardown step rides the same
    wall-clock bound, so the launched entry still returns promptly and the
    operation_runs row still completes."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setattr(mr, "start_meeting_scribe", lambda *a, **k: _NullScribe())
    monkeypatch.setattr(im_runtime, "load_meeting_map", _LoaderRecorder(None))
    # The tiny injectable teardown bound: with the kill genuinely bounded the whole
    # teardown finishes in well under a second; an UNbounded kill wedges forever.
    monkeypatch.setattr(prov, "ENGINE_TEARDOWN_TIMEOUT_S", 0.1)

    async def _fake_claim(db: Any, meeting_id: str, op: str, *, created_by: Any = None) -> str:
        return "run-1"

    monkeypatch.setattr(prov, "claim_meeting", _fake_claim)

    conn = FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row())
    db = FakeDb(conn)
    registry = mr.MeetingRuntimeRegistry(db)
    backend = FakeSandboxBackend()
    backend.handle = HangingKillSandboxHandle()
    pipe = FakeSpeakPipe()

    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-7"}}
    task = asyncio.ensure_future(
        prov.run_meeting_until_end(
            payload,
            db=db,
            registry=registry,
            timeout_s=5.0,
            provider=FakeProvider(),
            transport=FakeTransport(),
            speak=pipe,
            disambiguate=_confirm_every_hit,
            sandbox_backend=backend,
            model="claude-orch-test",
        )
    )

    runtime = None
    for _ in range(300):
        runtime = registry.get("m-1")
        if runtime is not None and getattr(runtime, "engine", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert runtime is not None and runtime.engine is not None, "the boot did not assemble the engine"

    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    # RED on an unbounded kill: the teardown wedges on sandbox.kill() and this trips.
    outcome = await asyncio.wait_for(task, timeout=3.0)

    assert outcome.ran_to_end is True
    handle = backend.handle
    assert isinstance(handle, HangingKillSandboxHandle) and handle.kill_started.is_set(), (
        "the kill must still be ATTEMPTED (bounded, not skipped)"
    )
    assert pipe.closed is True, "the speak pipe still closes ahead of the hung kill"
    assert registry.get("m-1") is None, "the runtime must still be dropped behind a hung kill"
    assert any("completed" in sql for sql, _ in conn.executed), (
        "the operation_runs row must still complete behind a hung sandbox kill"
    )


# ── AC6: the Output-Media router is mounted on the control-plane app ──────────


def test_control_plane_serves_output_media_page() -> None:
    """AC6 — GET /output-media/{meeting_id} is served by the control-plane host
    (the URL RECALL_OUTPUT_MEDIA_URL points at in deploy)."""
    from fastapi.testclient import TestClient

    from control_plane.app import create_app

    client = TestClient(create_app())
    resp = client.get("/output-media/m-cutover")
    assert resp.status_code == 200
    assert "orb" in resp.text
