"""provisioner — the per-meeting join spine (claim + assemble + run to end).

The provisioner turns a Recall in_call webhook into a running, atomically-claimed meeting:
resolve the bot -> atomic claim (win owns it / loss backs off) -> provision the workroom +
connection + reactive session -> keep-warm the sandbox -> run until meeting end -> teardown.

These exercise the REAL provisioner with the vendor + DB seams faked:
  * the atomic claim (``claim_meeting``) is faked to return a win / a loss deterministically
  * ``_assemble_workroom`` is faked so the claim/registry/keep-warm/run logic is isolated
    (the REAL _assemble_workroom is already proven end-to-end in test_workroom_bootpath.py)
  * the DB / repos.meetings lookup is faked to resolve the bot to a meeting row
No sandbox, no Recall, no Cartesia, no Anthropic.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from control_plane.meeting_runtime import MeetingRuntimeRegistry


class _FakeAcquire:
    async def __aenter__(self) -> object:
        return SimpleNamespace()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeDB:
    instance_id = "inst-test"

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


def _resolve_to(meeting_id: str = "m-1", repo_id: Any = 42) -> Any:
    async def _fake_repo_lookup(conn: object, bot_id: str) -> dict[str, Any] | None:
        return {"id": meeting_id, "repo_id": repo_id, "pinned_sha": None}

    return _fake_repo_lookup


def _in_call_payload(bot_id: str = "bot-1") -> dict[str, Any]:
    return {"event": "bot.in_call", "data": {"bot_id": bot_id}}


def _install_fakes(
    monkeypatch: Any,
    *,
    claim_result: Any,
    assemble_result: Any = None,
    resolve: Any = None,
    assemble_raises: bool = False,
) -> dict[str, Any]:
    """Patch claim_meeting + _assemble_workroom + repos.meetings.get_by_bot_id on the provisioner."""
    from control_plane import provisioner
    from libs.db import repos

    calls: dict[str, Any] = {"claim": 0, "assemble": 0, "keepwarm_args": []}

    async def _fake_claim(db: Any, meeting_id: str, op: str, *, created_by: str) -> Any:
        calls["claim"] += 1
        calls["claim_meeting_id"] = meeting_id
        return claim_result

    monkeypatch.setattr(provisioner, "claim_meeting", _fake_claim)

    if assemble_result is None:
        assemble_result = (None, None, None, SimpleNamespace())

    async def _fake_assemble(resolved: dict[str, Any], **kw: Any) -> Any:
        calls["assemble"] += 1
        calls["assemble_kwargs"] = kw
        if assemble_raises:
            raise RuntimeError("assembly exploded")
        return assemble_result

    monkeypatch.setattr(provisioner, "_assemble_workroom", _fake_assemble)
    monkeypatch.setattr(repos.meetings, "get_by_bot_id", resolve or _resolve_to())

    # Neutralize the real keep-warm task so a won-claim test does not spawn a live sleeper.
    async def _fake_keepwarm(workroom: Any, meeting_id: str, **kw: Any) -> None:
        calls["keepwarm_args"].append(meeting_id)

    monkeypatch.setattr(provisioner, "_sandbox_keepwarm", _fake_keepwarm)
    return calls


def test_atomic_claim_win_registers_a_runtime(monkeypatch) -> None:
    """A non-null claim id -> THIS instance owns the meeting: it assembles + registers a runtime
    and returns claimed=True with the run id."""
    from control_plane import provisioner

    workroom = SimpleNamespace(sandbox_id="sbx-1")
    calls = _install_fakes(
        monkeypatch,
        claim_result="run-77",
        assemble_result=(SimpleNamespace(), workroom, SimpleNamespace(), SimpleNamespace()),
    )

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        outcome = await provisioner.provision_meeting(
            _in_call_payload(), db=_FakeDB(), registry=registry
        )
        assert outcome.claimed is True
        assert outcome.run_id == "run-77"
        assert calls["claim"] == 1
        rt = registry.get("m-1")
        assert rt is not None and rt.workroom is workroom
        assert rt.session is not None and rt.connection is not None
        # keep-warm was spawned for the won meeting (a workroom exists) — the handle is stashed
        # on the runtime so meeting-end can cancel it before the kill:
        assert rt.sandbox_keepwarm is not None
        await asyncio.sleep(0)                      # let the scheduled keep-warm task run
        assert calls["keepwarm_args"] == ["m-1"]
        rt.sandbox_keepwarm.cancel()               # tidy the neutralized task

    asyncio.run(_run())


def test_atomic_claim_loss_opens_no_runtime(monkeypatch) -> None:
    """A None claim id (a concurrent/existing harness owns it) -> back off: claimed=False, NO
    runtime registered, NO assembly attempted."""
    from control_plane import provisioner

    calls = _install_fakes(monkeypatch, claim_result=None)

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        outcome = await provisioner.provision_meeting(
            _in_call_payload(), db=_FakeDB(), registry=registry
        )
        assert outcome.claimed is False
        assert calls["claim"] == 1
        assert calls["assemble"] == 0        # never assembled on a loss
        assert registry.get("m-1") is None   # no second runtime

    asyncio.run(_run())


def test_redelivered_in_call_is_idempotent_no_second_claim(monkeypatch) -> None:
    """An in_call for a meeting whose runtime is already registered on THIS instance must NOT
    re-claim or re-wire — it returns claimed=False before touching the claim substrate."""
    from control_plane import provisioner
    from control_plane.meeting_runtime import MeetingRuntime

    calls = _install_fakes(monkeypatch, claim_result="run-1")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        registry.register(MeetingRuntime(meeting_id="m-1"))  # already-owned meeting
        outcome = await provisioner.provision_meeting(
            _in_call_payload(), db=_FakeDB(), registry=registry
        )
        assert outcome.claimed is False
        assert calls["claim"] == 0   # never even attempted the claim (idempotent)

    asyncio.run(_run())


def test_non_liveness_event_and_unknown_bot_are_safe_no_ops(monkeypatch) -> None:
    """A non-liveness event (bot-status noise / an ended event), a payload with no bot, and
    an unresolvable bot each no-op with claimed=False and never raise on the webhook path.
    (A transcript event IS a liveness signal and provisions — dedicated test below.)"""
    from control_plane import provisioner
    from libs.db import repos

    _install_fakes(monkeypatch, claim_result="run-1")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        # not a liveness event — status noise / ended events never provision:
        o1 = await provisioner.provision_meeting(
            {"event": "recording.started", "data": {"bot_id": "b"}},
            db=_FakeDB(),
            registry=registry,
        )
        assert o1.claimed is False
        o1b = await provisioner.provision_meeting(
            {"event": "bot.call_ended", "data": {"bot_id": "b"}},
            db=_FakeDB(),
            registry=registry,
        )
        assert o1b.claimed is False
        # in_call but no bot id:
        o2 = await provisioner.provision_meeting(
            {"event": "in_call", "data": {}}, db=_FakeDB(), registry=registry
        )
        assert o2.claimed is False
        # in_call, valid bot, but the bot resolves to no meeting:
        async def _none(conn: object, bot_id: str) -> None:
            return None

        monkeypatch.setattr(repos.meetings, "get_by_bot_id", _none)
        o3 = await provisioner.provision_meeting(
            _in_call_payload(), db=_FakeDB(), registry=registry
        )
        assert o3.claimed is False

    asyncio.run(_run())


def test_transcript_event_is_liveness_and_provisions(monkeypatch) -> None:
    """THE general liveness contract: a realtime transcript delivery for a bot WE launched
    provisions the runtime exactly like an in_call — Recall only sends bot-status to the
    account-level dashboard webhook (a per-deployment config the product must not depend
    on), so the first per-bot realtime event must claim + provision. AND the words that
    event carries — the meeting's first utterance — must reach the wired session (the
    drain could not feed them: no runtime existed when it dispatched)."""
    from control_plane import provisioner

    class _RecordingSession:
        def __init__(self) -> None:
            self.lines: list[tuple[str, str, float, bool]] = []

        async def on_line(
            self, speaker: str, text: str, *, ts: float = 0.0, is_chat: bool = False
        ) -> None:
            self.lines.append((speaker, text, ts, is_chat))

    sess = _RecordingSession()
    calls = _install_fakes(
        monkeypatch,
        claim_result="run-1",
        assemble_result=(sess, SimpleNamespace(), None, SimpleNamespace()),
    )

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        # the REAL realtime envelope: bot nested as an object + the utterance under data.data
        outcome = await provisioner.provision_meeting(
            {
                "event": "transcript.data",
                "data": {
                    "bot": {"id": "bot-1"},
                    "data": {
                        "words": [{"text": "Hey"}, {"text": "Proxy,"}, {"text": "hello?"}],
                        "participant": {"name": "Riya"},
                    },
                },
            },
            db=_FakeDB(),
            registry=registry,
        )
        assert outcome.claimed is True
        assert calls["claim"] == 1
        assert registry.get("m-1") is not None  # the runtime is live
        # the trigger utterance reached the session, chronologically first, voice-rule
        assert sess.lines == [("Riya", "Hey Proxy, hello?", 0.0, False)]

    asyncio.run(_run())


def test_assembly_fault_keeps_the_claim_but_boots_without_a_brain(monkeypatch) -> None:
    """§3.8 honest-degrade: an assembly fault must NOT strand the claimed meeting or crash the
    webhook path — the runtime stays registered (claimed) but with no workroom/session."""
    from control_plane import provisioner

    calls = _install_fakes(monkeypatch, claim_result="run-9", assemble_raises=True)

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        outcome = await provisioner.provision_meeting(
            _in_call_payload(), db=_FakeDB(), registry=registry
        )
        assert outcome.claimed is True      # the claim is kept
        rt = registry.get("m-1")
        assert rt is not None
        assert rt.workroom is None and rt.session is None   # booted without a brain
        assert calls["keepwarm_args"] == []                 # no keep-warm without a workroom

    asyncio.run(_run())


# ── _assemble_workroom honest-degrade edges (token + repo gates) ──────────────────


class _AssembleDB:
    """A DB whose repo lookup returns a bound (or unbound) repo row for _assemble_workroom."""

    def __init__(self, full_name: str | None = "calcom/cal.com") -> None:
        self._full_name = full_name

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


def _patch_repo(monkeypatch: Any, full_name: str | None) -> None:
    from libs.db import repos

    async def _repo(conn: object, repo_id: object) -> dict[str, Any] | None:
        if full_name is None:
            return None
        return {"id": repo_id, "tenant_id": "t1", "full_name": full_name}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _repo)


def test_assemble_degrades_without_a_token(monkeypatch) -> None:
    """No subscription token -> (None, None, None, speak_pipe): the meeting keeps a voice channel
    but has no workroom brain (honest degrade, never a crash)."""
    import in_meeting.speak as speakmod
    from control_plane import provisioner

    pipe = SimpleNamespace(name="pipe")
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    _patch_repo(monkeypatch, "calcom/cal.com")

    async def _run() -> None:
        resolved = {"id": "m-2", "repo_id": 42, "pinned_sha": None}
        session, workroom, connection, speak_pipe = await provisioner._assemble_workroom(
            resolved, db=_AssembleDB(), bot_id="b", transport=SimpleNamespace(), oauth_token=None
        )
        assert (session, workroom, connection) == (None, None, None)
        assert speak_pipe is pipe

    asyncio.run(_run())


def test_assemble_degrades_without_a_bound_repo(monkeypatch) -> None:
    """A token but no bound repo (the repo row resolves to None) -> still (None, None, None,
    speak_pipe): nothing to clone, so no workroom, but the meeting still boots."""
    import in_meeting.speak as speakmod
    from control_plane import provisioner

    pipe = SimpleNamespace(name="pipe")
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)
    _patch_repo(monkeypatch, None)  # unbound repo

    async def _run() -> None:
        resolved = {"id": "m-3", "repo_id": 42, "pinned_sha": None}
        session, workroom, connection, speak_pipe = await provisioner._assemble_workroom(
            resolved, db=_AssembleDB(), bot_id="b", transport=SimpleNamespace(),
            oauth_token="sk-oauth",
        )
        assert (session, workroom, connection) == (None, None, None)
        assert speak_pipe is pipe

    asyncio.run(_run())


def test_resolve_map_text_prefers_pinned_then_latest_and_is_tenant_scoped(monkeypatch) -> None:
    """MAP-LOAD: the pre-meeting REPO_MAP is loaded from map_store, preferring the exact pinned-sha
    map and falling back to the latest, ALWAYS tenant-scoped (PM-STORE-02). A repo with neither
    yields None (an unmapped repo simply has no map — the meeting is unaffected)."""
    from control_plane import provisioner
    from libs.db import repos
    from premeeting import map_store

    seen: dict[str, Any] = {}

    async def _repo(conn: object, repo_id: object) -> dict[str, Any]:
        return {"id": repo_id, "tenant_id": "tenant-A", "full_name": "calcom/cal.com"}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _repo)

    async def _load_map(conn: object, *, tenant_id: str, repo: str, sha: str) -> str | None:
        seen["exact"] = (tenant_id, repo, sha)
        return "# pinned map" if sha == "deadbeef" else None

    async def _load_latest(conn: object, *, tenant_id: str, repo: str) -> Any:
        seen["latest"] = (tenant_id, repo)
        return ("shaX", "# latest map")

    monkeypatch.setattr(map_store, "load_map", _load_map)
    monkeypatch.setattr(map_store, "load_latest_map", _load_latest)

    async def _run() -> None:
        # (a) exact pinned-sha map wins, tenant-scoped:
        got = await provisioner._resolve_map_text(
            {"repo_id": 1, "pinned_sha": "deadbeef"}, db=_AssembleDB()
        )
        assert got == "# pinned map"
        assert seen["exact"] == ("tenant-A", "cal.com", "deadbeef")
        # (b) no pinned sha -> latest fallback, still tenant-scoped:
        got2 = await provisioner._resolve_map_text(
            {"repo_id": 1, "pinned_sha": None}, db=_AssembleDB()
        )
        assert got2 == "# latest map"
        assert seen["latest"] == ("tenant-A", "cal.com")

    asyncio.run(_run())


def test_resolve_map_text_returns_none_for_an_unmapped_repo(monkeypatch) -> None:
    """A repo_id of None, or a repo store that has no map, resolves to None without raising —
    the meeting boots with no REPO_MAP (honest degrade)."""
    from control_plane import provisioner
    from libs.db import repos
    from premeeting import map_store

    async def _repo(conn: object, repo_id: object) -> dict[str, Any]:
        return {"id": repo_id, "tenant_id": "t", "full_name": "x/y"}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _repo)

    async def _none_latest(conn: object, *, tenant_id: str, repo: str) -> None:
        return None

    monkeypatch.setattr(map_store, "load_latest_map", _none_latest)

    async def _run() -> None:
        # no repo bound at all:
        assert await provisioner._resolve_map_text({"repo_id": None}, db=_AssembleDB()) is None
        # bound but unmapped:
        assert await provisioner._resolve_map_text(
            {"repo_id": 1, "pinned_sha": None}, db=_AssembleDB()
        ) is None

    asyncio.run(_run())


def test_meeting_info_md_renders_participants_verbatim_and_honestly() -> None:
    """MEETING_INFO.md is built from the in_call payload's title + participant names VERBATIM;
    a payload with no metadata renders the honest empty body."""
    from control_plane.provisioner import _meeting_info_md

    md = _meeting_info_md(
        {"data": {"title": "Release sync", "participants": [{"name": "Ann"}, {"name": "Bob"}, {}]}}
    )
    assert "Release sync" in md and "- Ann" in md and "- Bob" in md
    # empty payload -> honest "(no meeting metadata available)", never synthesized:
    md2 = _meeting_info_md({"data": {}})
    assert "no meeting metadata" in md2.lower()


def test_meeting_max_s_env_override_and_safe_fallback(monkeypatch) -> None:
    """The safety-ceiling resolver honors MEETING_MAX_HOURS, and falls back to the generous
    default for an unset / unparsable / non-positive value (never unbounded-by-typo)."""
    from control_plane import provisioner

    monkeypatch.setenv("MEETING_MAX_HOURS", "6")
    assert provisioner._meeting_max_s() == 6 * 3600.0
    monkeypatch.setenv("MEETING_MAX_HOURS", "not-a-number")
    assert provisioner._meeting_max_s() == provisioner.DEFAULT_MEETING_MAX_HOURS * 3600.0
    monkeypatch.setenv("MEETING_MAX_HOURS", "0")
    assert provisioner._meeting_max_s() == provisioner.DEFAULT_MEETING_MAX_HOURS * 3600.0
    monkeypatch.delenv("MEETING_MAX_HOURS", raising=False)
    assert provisioner._meeting_max_s() == provisioner.DEFAULT_MEETING_MAX_HOURS * 3600.0


def test_run_meeting_until_end_returns_early_on_a_claim_loss(monkeypatch) -> None:
    """A claim loss short-circuits run_meeting_until_end (no run loop, ran_to_end stays False)."""
    from control_plane import provisioner

    _install_fakes(monkeypatch, claim_result=None)

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        outcome = await provisioner.run_meeting_until_end(
            _in_call_payload(), db=_FakeDB(), registry=registry, timeout_s=0.1
        )
        assert outcome.claimed is False and outcome.ran_to_end is False

    asyncio.run(_run())


def test_run_meeting_until_end_ends_when_the_runtime_is_dropped(monkeypatch) -> None:
    """The run loop waits on the registry drop (the meeting-end signal). When end_meeting drops
    the runtime, the loop completes with ran_to_end=True and completes the operation_runs row."""
    from control_plane import provisioner
    from control_plane.meeting_runtime import MeetingRuntime

    calls = _install_fakes(
        monkeypatch,
        claim_result="run-end",
        assemble_result=(SimpleNamespace(), SimpleNamespace(sandbox_id="s"), SimpleNamespace(),
                         SimpleNamespace()),
    )
    completed: list[Any] = []

    async def _fake_complete(db: Any, run_id: Any) -> None:
        completed.append(run_id)

    monkeypatch.setattr(provisioner, "_complete_run", _fake_complete)

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())

        async def _drop_soon() -> None:
            await asyncio.sleep(0.05)
            # emulate the meeting-end webhook dropping the runtime:
            registry._runtimes.pop("m-1", None)

        asyncio.ensure_future(_drop_soon())
        outcome = await provisioner.run_meeting_until_end(
            _in_call_payload(), db=_FakeDB(), registry=registry, timeout_s=2.0
        )
        assert outcome.claimed is True
        assert outcome.ran_to_end is True
        assert completed == ["run-end"]   # the run row was completed
        assert calls["keepwarm_args"] == ["m-1"]

    asyncio.run(_run())


def test_run_meeting_until_end_safety_ceiling_tears_down_on_timeout(monkeypatch) -> None:
    """If the meeting-end webhook never arrives, the generous safety ceiling fires: the loop tears
    the runtime down itself (end_meeting with reason 'safety_ceiling') and completes the row."""
    from control_plane import provisioner

    _install_fakes(
        monkeypatch,
        claim_result="run-leak",
        assemble_result=(SimpleNamespace(), SimpleNamespace(sandbox_id="s"), SimpleNamespace(),
                         SimpleNamespace()),
    )
    monkeypatch.setattr(provisioner, "_complete_run", _noop_complete())

    ended: list[tuple[str, str]] = []

    class _Registry(MeetingRuntimeRegistry):
        async def end_meeting(self, meeting_id: str, *, reason: str = "call_ended",
                              timeout_s: float | None = None) -> None:
            ended.append((meeting_id, reason))
            self._runtimes.pop(meeting_id, None)

    async def _run() -> None:
        registry = _Registry(_FakeDB())
        outcome = await provisioner.run_meeting_until_end(
            _in_call_payload(), db=_FakeDB(), registry=registry, timeout_s=0.05
        )
        assert outcome.claimed is True
        assert outcome.ran_to_end is False
        assert ended == [("m-1", "safety_ceiling")]   # the leak backstop tore it down

    asyncio.run(_run())


def _noop_complete() -> Any:
    async def _c(db: Any, run_id: Any) -> None:
        return None

    return _c


# ── keep-warm heartbeat + meeting-end teardown ordering ──────────────────────────


def test_sandbox_keepwarm_extends_the_sandbox_and_never_throws(monkeypatch) -> None:
    """Each beat extends the sandbox lifetime via set_timeout through the call_external seam; a
    failing beat logs and the loop keeps beating (never-throw). Cancellation propagates cleanly."""
    from control_plane import provisioner

    extended: list[int] = []
    fail_first = {"n": 0}

    class _Sandbox:
        async def set_timeout(self, seconds: int) -> None:
            fail_first["n"] += 1
            if fail_first["n"] == 1:
                raise RuntimeError("e2b blip")   # the first beat fails -> logged, loop continues
            extended.append(seconds)

    workroom = SimpleNamespace(sandbox=_Sandbox())

    async def _run() -> None:
        # A tiny injected interval so the loop beats quickly in the test.
        task = asyncio.ensure_future(
            provisioner._sandbox_keepwarm(workroom, "m-warm", interval_s=0.01)
        )
        await asyncio.sleep(0.05)   # allow several beats (first fails, rest succeed)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # at least one successful extension landed at the real SANDBOX_TIMEOUT_S despite the fault:
        assert extended and extended[0] == int(provisioner.SANDBOX_TIMEOUT_S)

    asyncio.run(_run())


def test_end_meeting_cancels_keepwarm_before_killing_the_sandbox(monkeypatch) -> None:
    """§3.8 teardown ordering: end_meeting cancels the keep-warm heartbeat FIRST (so no beat
    re-extends a sandbox mid-teardown), then drains the session, closes the pipe, kills the
    sandbox, and drops the runtime. Proven with a real running keep-warm task."""
    from control_plane.meeting_runtime import MeetingRuntime, MeetingRuntimeRegistry

    order: list[str] = []

    class _Session:
        async def drain(self) -> None:
            order.append("drain")

    class _Pipe:
        async def aclose(self) -> None:
            order.append("pipe_close")

    class _Workroom:
        async def teardown(self) -> None:
            order.append("sandbox_kill")

    async def _run() -> None:
        keepwarm_cancelled = asyncio.Event()

        async def _keepwarm() -> None:
            try:
                while True:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                order.append("keepwarm_cancel")
                keepwarm_cancelled.set()
                raise

        registry = MeetingRuntimeRegistry(_FakeDB())
        task = asyncio.ensure_future(_keepwarm())
        await asyncio.sleep(0)  # let it start
        runtime = MeetingRuntime(
            meeting_id="m-td",
            session=_Session(),
            workroom=_Workroom(),
            speak_pipe=_Pipe(),
            sandbox_keepwarm=task,
        )
        registry.register(runtime)

        await registry.end_meeting("m-td", reason="call_ended", timeout_s=1.0)

        # keep-warm cancel is FIRST; sandbox kill comes AFTER the drain + pipe close:
        assert order[0] == "keepwarm_cancel"
        assert order.index("drain") < order.index("sandbox_kill")
        assert order.index("pipe_close") < order.index("sandbox_kill")
        assert registry.get("m-td") is None          # runtime dropped
        # idempotent: a second end is a safe no-op (no new teardown steps):
        before = list(order)
        await registry.end_meeting("m-td")
        assert order == before

    asyncio.run(_run())


def test_end_meeting_teardown_is_bounded_and_survives_a_hung_step(monkeypatch) -> None:
    """A hung teardown step (e.g. a wedged sandbox kill) is abandoned after the bound rather than
    blocking the drop behind it — the runtime is still dropped (never-deadlock)."""
    from control_plane.meeting_runtime import MeetingRuntime, MeetingRuntimeRegistry

    class _HungWorkroom:
        async def teardown(self) -> None:
            await asyncio.sleep(60)   # would hang forever without the wall-clock bound

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(_FakeDB())
        registry.register(MeetingRuntime(meeting_id="m-hung", workroom=_HungWorkroom()))
        # a tiny bound so the hung kill is abandoned quickly; the drop must still complete:
        await asyncio.wait_for(
            registry.end_meeting("m-hung", timeout_s=0.05), timeout=2.0
        )
        assert registry.get("m-hung") is None

    asyncio.run(_run())
