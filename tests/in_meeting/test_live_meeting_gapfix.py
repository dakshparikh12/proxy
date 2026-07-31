"""Gap Pass #1 fixes on the LIVE meeting path (CRIT-2 / CRIT-1 / IMP-3).

Three gaps, one file — all on the provisioner/webhook-drain spine:

* **CRIT-2 — no 1h wall-clock kill.** SPEC §9: a meeting has NO time cap. The old
  ``DEFAULT_MEETING_TIMEOUT_S = 3600.0`` force-closed every meeting at 60 minutes.
  The meeting must run until the explicit ``MeetingEnd`` signal; the only remaining
  wall clock is a GENEROUS env-configurable safety ceiling (``MEETING_MAX_HOURS``,
  default 12h) so a wedged loop can never leak an instance forever.

* **CRIT-1 — barge-in wired on the transcript-driven boot path.** The reflex
  existed (``transport.turn`` → ``barge_in()``) but NOTHING on the new boot path
  fed a human-speech signal to the meeting's ``SpeakPipe.cut()``. The boot path is
  transcript-driven (Recall realtime webhooks: finals + partials + chat — no raw
  audio/VAD ingestion exists on it), so the wired trigger is the TRANSCRIPT-PARTIAL
  barge-in: a partial (or final) from a NON-Proxy speaker landing while THIS
  meeting's ``SpeakPipe`` is mid-utterance cuts the pipe. Guards: never on Proxy's
  own speaker label; never with no active utterance.

* **IMP-3 — sandbox keep-warm for >1h meetings.** The E2B sandbox self-times-out
  at ``SANDBOX_TIMEOUT_S`` (3600s). With CRIT-2 removing the meeting cap, a keep-warm
  heartbeat periodically extends the sandbox's lifetime (``set_timeout``) while the
  meeting is live; it is cancelled at teardown before the kill, and a heartbeat
  fault logs — never crashes the meeting (never-throw).

Offline + deterministic: every vendor seam is a fake; the db is an in-memory
stand-in (the same pattern as ``test_cutover.py``).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting.sandbox import SANDBOX_TIMEOUT_S
from in_meeting.speak import SpeakPipe, build_speak_sink

# ── shared fakes (mirroring test_cutover.py's minimal seams) ─────────────────


def _happy_turn() -> list[AgentChunk]:
    return [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "s-1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="TEXT", text="on it", metadata={"msg_id": "m-1"}),
        AgentChunk(type="RESULT", text="on it", metadata={"session_id": "s-1", "total_cost_usd": 0.01}),
    ]


class FakeProvider:
    def __init__(self, turns: Sequence[Sequence[AgentChunk]] | None = None) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._turns: list[list[AgentChunk]] = [list(t) for t in (turns or [_happy_turn()])]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        script = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        for chunk in script:
            yield chunk


class FakeTransport:
    async def mute(self, bot_id: str) -> None: ...

    async def unmute(self, bot_id: str) -> None: ...

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...


class FakeSandboxHandle:
    """The provisioned E2B handle shape: commands/files/kill + set_timeout recorder."""

    def __init__(self, *, set_timeout_fails: bool = False) -> None:
        self.killed = False
        self.set_timeout_calls: list[int] = []
        self._set_timeout_fails = set_timeout_fails

    @property
    def commands(self) -> Any:
        return None

    @property
    def files(self) -> Any:
        return None

    async def kill(self) -> None:
        self.killed = True

    async def set_timeout(self, timeout: int) -> None:
        self.set_timeout_calls.append(timeout)
        if self._set_timeout_fails:
            raise RuntimeError("e2b set_timeout blew up (scripted)")


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.handle = FakeSandboxHandle()

    async def __call__(self, **kwargs: Any) -> FakeSandboxHandle:
        return self.handle


class FakeSpeakPipe:
    """The speak seam on boot-path tests: records aclose (never speaks)."""

    def __init__(self) -> None:
        self.closed = False

    async def say(self, text: str) -> None: ...

    async def aclose(self) -> None:
        self.closed = True


async def _confirm_every_hit(line: str) -> bool:
    return True


class _LoaderRecorder:
    def __init__(self, map_text: str | None = None) -> None:
        self.map_text = map_text

    async def __call__(self, *, conn: Any, tenant_id: str, repo: str, pinned_sha: str) -> str | None:
        return self.map_text


class _NullScribe:
    async def aclose(self) -> None:
        return None

    async def wait(self) -> None:
        return None


class FakeConn:
    def __init__(
        self,
        *,
        meeting_row: dict[str, Any] | None = None,
        repo_row: dict[str, Any] | None = None,
    ) -> None:
        self.meeting_row = meeting_row
        self.repo_row = repo_row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM meetings" in sql:
            return self.meeting_row
        if "FROM repos" in sql:
            return self.repo_row
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "UPDATE 1"


class FakeDb:
    instance_id = "gapfix-test-instance"

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


def _resolved_row() -> dict[str, Any]:
    return {
        "id": "m-1",
        "tenant_id": "tenant-1",
        "repo_id": "r-1",
        "pinned_sha": "abc123",
        "recall_bot_id": "bot-7",
        "meeting_url": "https://meet.example/x",
    }


def _repo_row() -> dict[str, Any]:
    return {"id": "r-1", "tenant_id": "tenant-1", "full_name": "acme/widget", "default_branch": "main"}


def _boot_patches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The AC5-style offline boot: null scribe, recorded map loader, fake claim."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov
    from in_meeting import runtime as im_runtime

    monkeypatch.setenv("PROXY_TENANT_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setattr(mr, "start_meeting_scribe", lambda *a, **k: _NullScribe())
    monkeypatch.setattr(im_runtime, "load_meeting_map", _LoaderRecorder(None))

    async def _fake_claim(db: Any, meeting_id: str, op: str, *, created_by: Any = None) -> str:
        return "run-1"

    monkeypatch.setattr(prov, "claim_meeting", _fake_claim)


async def _wait_for_boot(registry: Any, meeting_id: str = "m-1") -> Any:
    runtime = None
    for _ in range(300):
        runtime = registry.get(meeting_id)
        if runtime is not None and getattr(runtime, "engine", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert runtime is not None and runtime.engine is not None, "the boot did not assemble the engine"
    return runtime


# ══ CRIT-2: the 1h hard meeting cap is GONE (SPEC §9 — no time cap) ══════════


def test_one_hour_kill_is_gone_and_ceiling_is_env_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 60-min wall-clock kill is REMOVED: the old ``DEFAULT_MEETING_TIMEOUT_S``
    constant no longer exists, and the only remaining wall clock is the generous
    env-configurable safety ceiling — ``MEETING_MAX_HOURS`` (default 12h), never
    a hard-coded hour."""
    from control_plane import provisioner as prov

    assert not hasattr(prov, "DEFAULT_MEETING_TIMEOUT_S"), (
        "the 3600s hard meeting cap must be deleted (SPEC §9: no time cap)"
    )

    monkeypatch.delenv("MEETING_MAX_HOURS", raising=False)
    assert prov._meeting_max_s() == pytest.approx(12.0 * 3600.0), (
        "the default safety ceiling must be generous (12h), not the old hour"
    )

    monkeypatch.setenv("MEETING_MAX_HOURS", "2.5")
    assert prov._meeting_max_s() == pytest.approx(9000.0)

    monkeypatch.setenv("MEETING_MAX_HOURS", "not-a-number")
    assert prov._meeting_max_s() == pytest.approx(12.0 * 3600.0)

    monkeypatch.setenv("MEETING_MAX_HOURS", "-1")
    assert prov._meeting_max_s() == pytest.approx(12.0 * 3600.0), (
        "a non-positive ceiling falls back to the generous default (never unbounded-by-typo)"
    )


@pytest.mark.asyncio
async def test_meeting_end_still_ends_the_uncapped_loop_promptly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With NO explicit timeout (the production launcher path) the loop waits on
    the explicit ``MeetingEnd`` signal — and ends promptly when it lands. The
    simulated >1h meeting: the resolver is monkeypatched to prove the None path
    consults it (not a hard 3600), and the ceiling handed back is far past 1h."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov

    _boot_patches(monkeypatch, tmp_path)
    monkeypatch.delenv("MEETING_MAX_HOURS", raising=False)

    resolved_bounds: list[float] = []
    real_resolver = prov._meeting_max_s

    def _spy_resolver() -> float:
        bound = real_resolver()
        resolved_bounds.append(bound)
        return bound

    monkeypatch.setattr(prov, "_meeting_max_s", _spy_resolver)

    conn = FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row())
    db = FakeDb(conn)
    registry = mr.MeetingRuntimeRegistry(db)
    backend = FakeSandboxBackend()

    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-7"}}
    task = asyncio.ensure_future(
        prov.run_meeting_until_end(
            payload,
            db=db,
            registry=registry,
            provider=FakeProvider(),
            transport=FakeTransport(),
            speak=FakeSpeakPipe(),
            disambiguate=_confirm_every_hit,
            sandbox_backend=backend,
            model="claude-orch-test",
        )
    )

    runtime = await _wait_for_boot(registry)

    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    outcome = await asyncio.wait_for(task, timeout=5.0)

    assert outcome.ran_to_end is True, "MeetingEnd must still end the loop promptly"
    assert resolved_bounds and resolved_bounds[0] == pytest.approx(12.0 * 3600.0), (
        "the no-timeout path must resolve the env ceiling (12h default) — a meeting "
        "running past the old 1h mark is NOT force-closed"
    )
    assert backend.handle.killed is True
    assert any("completed" in sql for sql, _ in conn.executed)


@pytest.mark.asyncio
async def test_safety_ceiling_is_the_env_bound_and_still_tears_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The remaining ceiling is REAL and env-driven: with ``MEETING_MAX_HOURS``
    set tiny and no MeetingEnd ever emitted, the loop returns ``ran_to_end=False``
    and the full teardown (sandbox kill + operation row completion) still runs."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov

    _boot_patches(monkeypatch, tmp_path)
    monkeypatch.setenv("MEETING_MAX_HOURS", "0.0001")  # 0.36s — the test's stand-in ceiling

    conn = FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row())
    db = FakeDb(conn)
    registry = mr.MeetingRuntimeRegistry(db)
    backend = FakeSandboxBackend()
    pipe = FakeSpeakPipe()

    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-7"}}
    outcome = await asyncio.wait_for(
        prov.run_meeting_until_end(
            payload,
            db=db,
            registry=registry,
            provider=FakeProvider(),
            transport=FakeTransport(),
            speak=pipe,
            disambiguate=_confirm_every_hit,
            sandbox_backend=backend,
            model="claude-orch-test",
        ),
        timeout=10.0,
    )

    assert outcome.claimed is True
    assert outcome.ran_to_end is False, "the ceiling elapsed — honestly reported, not ran_to_end"
    assert pipe.closed is True
    assert backend.handle.killed is True
    assert any("completed" in sql for sql, _ in conn.executed), (
        "the operation row must still complete when the safety ceiling trips"
    )


# ══ CRIT-1: transcript-partial barge-in — human speech cuts Proxy's audio ════


class _RecorderChannel:
    """An Output-Media channel recorder (the AudioOut shape)."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.speaking: bool | None = None

    async def write_audio(self, pcm: bytes) -> None:
        self.writes.append(pcm)

    async def set_speaking(self, speaking: bool) -> None:
        self.speaking = speaking


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm


def _slow_synth(sentence: str) -> AsyncIterator[_Chunk]:
    async def _gen() -> AsyncIterator[_Chunk]:
        for _ in range(200):
            yield _Chunk(b"\x01\x02")
            await asyncio.sleep(0.01)

    return _gen()


class _CutRecorderPipe:
    """A pipe stand-in exposing exactly the cut-trigger surface (speaking + cut)."""

    def __init__(self, *, speaking: bool) -> None:
        self.speaking = speaking
        self.cuts = 0

    async def cut(self) -> None:
        self.cuts += 1


class _DispatchRuntime:
    """The registry entry the webhook dispatch reaches: pipe + notes ingest."""

    def __init__(self, speak_pipe: Any, engine: Any = None) -> None:
        self.speak_pipe = speak_pipe
        self.engine = engine
        self.ingested: list[dict[str, Any]] = []

    async def ingest_transcript(self, body: dict[str, Any]) -> None:
        self.ingested.append(body)


class _DispatchRegistry:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def get(self, meeting_id: str) -> Any:
        return self._runtime


def _partial_payload(speaker: str, words: str = "wait, actually—") -> dict[str, Any]:
    return {
        "event": "transcript.partial_data",
        "data": {"bot_id": "bot-7", "words": words, "speaker": speaker, "timestamp": 30.0},
    }


@pytest.mark.asyncio
async def test_human_partial_cuts_proxys_speech_mid_utterance() -> None:
    """THE reflex: a ``transcript.partial_data`` from a NON-Proxy speaker landing
    while the REAL ``SpeakPipe`` is mid-utterance cuts the audio NOW — the in-flight
    synth stops, no further pcm reaches the channel, speaking drops to False — and
    the partial STILL reaches the notes-plane ingest (the cut is additive)."""
    from control_plane.webhooks import _dispatch_meeting_event

    channel = _RecorderChannel()
    pipe = build_speak_sink(synthesize=_slow_synth, channel=channel)
    await pipe.say("Here is a long answer that keeps streaming audio.")
    for _ in range(200):
        if channel.writes:
            break
        await asyncio.sleep(0.005)
    assert channel.writes, "the fake synth never reached the channel (test setup)"
    assert pipe.speaking is True, "the pipe must expose mid-utterance speaking state"

    runtime = _DispatchRuntime(speak_pipe=pipe)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))
    await _dispatch_meeting_event(
        _partial_payload("Sam"), db=db, registry=_DispatchRegistry(runtime)
    )

    assert pipe.speaking is False, "a human partial mid-utterance must cut the pipe"
    assert channel.speaking is False, "the orb must drop with the cut"
    written_after_cut = len(channel.writes)
    await asyncio.sleep(0.05)
    assert len(channel.writes) == written_after_cut, "audio kept flowing after the cut"
    assert runtime.ingested and runtime.ingested[0]["words"] == "wait, actually—", (
        "the partial must still reach the notes-plane ingest"
    )


@pytest.mark.asyncio
async def test_proxys_own_partial_never_cuts() -> None:
    """Guard 1 — Proxy's own transcribed speech (speaker label ``Proxy``) must
    NEVER trigger the cut (AC-TURN-11: barge-in never fires on Proxy's own audio)."""
    from control_plane.webhooks import _dispatch_meeting_event
    from transport.hearing import PROXY_SPEAKER

    pipe = _CutRecorderPipe(speaking=True)
    runtime = _DispatchRuntime(speak_pipe=pipe)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))

    await _dispatch_meeting_event(
        _partial_payload(PROXY_SPEAKER), db=db, registry=_DispatchRegistry(runtime)
    )

    assert pipe.cuts == 0, "Proxy's own speaker label must never cut its speech"
    assert runtime.ingested, "the notes-plane ingest still receives Proxy's line"


@pytest.mark.asyncio
async def test_no_active_utterance_means_no_cut() -> None:
    """Guard 2 — a human partial with NOTHING mid-utterance (idle pipe, absent
    pipe, unlabelled speaker) never cuts and never raises."""
    from control_plane.webhooks import _dispatch_meeting_event

    idle_pipe = _CutRecorderPipe(speaking=False)
    runtime = _DispatchRuntime(speak_pipe=idle_pipe)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))
    await _dispatch_meeting_event(
        _partial_payload("Sam"), db=db, registry=_DispatchRegistry(runtime)
    )
    assert idle_pipe.cuts == 0, "an idle pipe must not be cut"

    # An assembled runtime with NO pipe (engine-less/notes-only meeting): safe no-op.
    no_pipe_runtime = _DispatchRuntime(speak_pipe=None)
    await _dispatch_meeting_event(
        _partial_payload("Sam"), db=db, registry=_DispatchRegistry(no_pipe_runtime)
    )

    # An unattributed partial (no speaker label) cannot be proven human: never cuts.
    speaking_pipe = _CutRecorderPipe(speaking=True)
    unlabelled = _DispatchRuntime(speak_pipe=speaking_pipe)
    await _dispatch_meeting_event(
        _partial_payload(""), db=db, registry=_DispatchRegistry(unlabelled)
    )
    assert speaking_pipe.cuts == 0, "an unattributed line must not cut (cannot prove non-Proxy)"


@pytest.mark.asyncio
async def test_human_final_mid_utterance_also_cuts() -> None:
    """Defensive fallback — a FINAL from a human mid-utterance also cuts (partials
    are the fast path, but a finals-only delivery still barges in)."""
    from control_plane.webhooks import _dispatch_meeting_event

    pipe = _CutRecorderPipe(speaking=True)
    runtime = _DispatchRuntime(speak_pipe=pipe)
    db = FakeDb(FakeConn(meeting_row=_resolved_row()))

    payload = {
        "event": "transcript.data",
        "data": {
            "bot_id": "bot-7",
            "words": "hang on Proxy",
            "speaker": "Sam",
            "timestamp": 31.0,
            "end_of_turn": True,
        },
    }
    await _dispatch_meeting_event(payload, db=db, registry=_DispatchRegistry(runtime))

    assert pipe.cuts == 1, "a human final mid-utterance must also cut"


@pytest.mark.asyncio
async def test_speak_pipe_exposes_mid_utterance_state() -> None:
    """The ``SpeakPipe.speaking`` surface the trigger guards on: False when idle,
    True across the whole in-flight utterance (queued text included), False after
    a cut."""
    channel = _RecorderChannel()
    pipe = SpeakPipe(synthesize=_slow_synth, channel=channel)
    assert pipe.speaking is False, "an idle pipe is not mid-utterance"

    await pipe.say("A full sentence to synthesize.")
    assert pipe.speaking is True, "queued/in-flight synth IS mid-utterance"

    await pipe.cut()
    assert pipe.speaking is False, "a cut pipe is idle again"


# ══ IMP-3: E2B sandbox keep-warm — >1h meetings keep code execution ═══════════


@pytest.mark.asyncio
async def test_keepwarm_heartbeat_extends_the_sandbox_lifetime() -> None:
    """The heartbeat calls the handle's ``set_timeout`` every interval with the
    full sandbox lifetime, so a live meeting keeps extending the sandbox."""
    from control_plane.provisioner import _sandbox_keepwarm

    handle = FakeSandboxHandle()
    task = asyncio.ensure_future(_sandbox_keepwarm(handle, "m-1", interval_s=0.02))
    for _ in range(200):
        if len(handle.set_timeout_calls) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(handle.set_timeout_calls) >= 2, "the heartbeat must fire repeatedly"
    assert all(t == SANDBOX_TIMEOUT_S for t in handle.set_timeout_calls), (
        "each beat extends by the full sandbox lifetime (set_timeout seconds)"
    )


@pytest.mark.asyncio
async def test_keepwarm_failure_logs_and_keeps_beating(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never-throw: a failing ``set_timeout`` logs and the heartbeat keeps
    running — a keep-warm fault must never crash the meeting."""
    from control_plane.provisioner import _sandbox_keepwarm

    handle = FakeSandboxHandle(set_timeout_fails=True)
    with caplog.at_level("WARNING"):
        task = asyncio.ensure_future(_sandbox_keepwarm(handle, "m-1", interval_s=0.02))
        for _ in range(200):
            if len(handle.set_timeout_calls) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)

    assert len(handle.set_timeout_calls) >= 2, "the loop must survive a failing beat"
    assert all(isinstance(r, asyncio.CancelledError) or r is None for r in results), (
        "no exception may escape the heartbeat (never-throw)"
    )
    assert any("keep-warm" in rec.getMessage() for rec in caplog.records), (
        "a failed beat must be logged for a human (never silent)"
    )


@pytest.mark.asyncio
async def test_boot_wires_keepwarm_and_teardown_cancels_it_before_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The provisioner spawns the keep-warm on a WON claim with a live sandbox,
    the beat actually lands while the meeting runs, and meeting end cancels the
    heartbeat before the sandbox kill."""
    from control_plane import meeting_runtime as mr
    from control_plane import provisioner as prov

    _boot_patches(monkeypatch, tmp_path)
    monkeypatch.setattr(prov, "SANDBOX_KEEPWARM_INTERVAL_S", 0.02)

    conn = FakeConn(meeting_row=_resolved_row(), repo_row=_repo_row())
    db = FakeDb(conn)
    registry = mr.MeetingRuntimeRegistry(db)
    backend = FakeSandboxBackend()

    payload = {"event": "bot.in_call", "data": {"bot_id": "bot-7"}}
    task = asyncio.ensure_future(
        prov.run_meeting_until_end(
            payload,
            db=db,
            registry=registry,
            timeout_s=10.0,
            provider=FakeProvider(),
            transport=FakeTransport(),
            speak=FakeSpeakPipe(),
            disambiguate=_confirm_every_hit,
            sandbox_backend=backend,
            model="claude-orch-test",
        )
    )

    runtime = await _wait_for_boot(registry)
    keepwarm = getattr(runtime, "sandbox_keepwarm", None)
    assert keepwarm is not None and not keepwarm.done(), (
        "the provisioner must spawn the keep-warm task for a live sandbox"
    )
    for _ in range(200):
        if backend.handle.set_timeout_calls:
            break
        await asyncio.sleep(0.01)
    assert backend.handle.set_timeout_calls, "the heartbeat must beat while the meeting is live"

    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    outcome = await asyncio.wait_for(task, timeout=5.0)

    assert outcome.ran_to_end is True
    assert keepwarm.done(), "teardown must cancel the keep-warm heartbeat"
    assert backend.handle.killed is True, "the kill still runs after the heartbeat stops"
