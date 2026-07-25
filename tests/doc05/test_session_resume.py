"""Doc 05 · workroom.session-resume — resume the SDK conversation across a
kill/restart via the IMPORTED ``agentkit.resume_with_fallback``; on resume failure
rebuild context from the Bundle (``history_fn = rebuild_from_bundle``) and continue;
an ABORTED task (imported ``agentkit.AbortRegistry``) is NEVER resumed (05 §3.1 /
§3.11 / CANONICAL §11.9).

Spec refs:
  * §3.1 — "The SDK session id is persisted per task so a follow-up (or a restart)
    resumes the same conversation; on resume failure the context is rebuilt from the
    bundle and the run continues (their stale-session-replay pattern). This replay is
    ``resume_with_fallback(session_id, history_fn)`` imported from
    ``libs/agentkit/resume.py`` (CANONICAL §11.9), not reimplemented here — Doc 04
    §3.5 imports the same function, parameterized by history source (here
    ``history_fn`` = rebuild-from-bundle)."
  * §3.11 — "The ``AbortRegistry`` ... is imported from ``libs/agentkit/abort.py``
    (CANONICAL §11.9), not reimplemented here. ... **Abort is FINAL, never retried** —
    so session-resume / JSON-retry can never resurrect a build the user killed
    mid-meeting."
  * §11.9 — the ONE definition site for ``resume_with_fallback`` + ``AbortRegistry``;
    Docs 04 and 05 IMPORT, neither redefines.

The DEFINITION OF DONE proven here (host path, in-process fakes; e2b NOT installed):
  Tier-1  a killed-and-restarted task RESUMES its SDK conversation via the imported
          ``resume_with_fallback`` (the persisted session id rides ``resume`` — the
          provider sees it; no history rebuild when the session is still alive).
  Tier-3  a FAILED resume (the SDK reports the session gone) rebuilds context from the
          Bundle via ``history_fn = rebuild_from_bundle`` and CONTINUES (a new session
          WITHOUT resume, carrying the bundle-derived preamble + the restored notice).
  abort   an ABORTED task (the imported ``AbortRegistry`` fired its controller) is
          NEVER resumed — resume short-circuits, the provider is never driven, no
          history rebuild resurrects the build the user killed.

Two NOT-done guards (the node's invariants, §11.9): the module must IMPORT
``resume_with_fallback`` + ``AbortRegistry`` from agentkit and never define its own —
asserted structurally on the module source.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest

from contracts import AgentChunk, Bundle, Envelope

from libs.ops import sandbox_provider


# ── in-process fakes: a provider that can resume, go stale, or be aborted ─────


class FakeResumeProvider:
    """A recording ``agentkit.Provider`` stub for the resume/fallback/abort paths.

    Satisfies the seam (``stream(prompt, query) -> AsyncIterator[AgentChunk]``) and,
    for §3.1/§3.5, models the SDK session lifecycle:

      * a LIVE resume — the provider sees ``query.resume`` = the persisted session id
        and streams normally (Tier-1: nothing was lost, so no history rebuild fires);
      * a GONE session — when primed with ``stale_on_resume``, a ``stream`` that carries
        a non-None ``resume`` yields an ``ERROR`` chunk whose message is a §3.5
        STALE_MARKER ("no conversation found with session id"), which the runner
        surfaces as ``ProviderError`` — exactly what ``resume_with_fallback``'s replay
        arm catches (Tier-3: rebuild from ``history_fn``, retry WITHOUT resume).

    It records every ``resume`` / ``prompt`` / ``preamble`` it saw so a test can prove
    the replay retried without resume and carried the bundle-derived preamble, and it
    exposes ``session_ids`` (each turn mints a fresh SDK session id on its INIT/RESULT).
    """

    name = "claude"

    def __init__(self, *, stale_on_resume: bool = False) -> None:
        self.stale_on_resume = stale_on_resume
        self.calls = 0
        self.seen_resume: list[str | None] = []
        self.seen_prompts: list[str] = []
        self.seen_preambles: list[str | None] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        call_no = self.calls
        resume = getattr(query, "resume", None)
        preamble = getattr(query, "preamble", None)
        self.seen_resume.append(resume)
        self.seen_prompts.append(prompt)
        self.seen_preambles.append(preamble)
        stale = self.stale_on_resume and resume is not None

        async def gen() -> AsyncIterator[AgentChunk]:
            sid = f"sess-{call_no}"
            yield AgentChunk(type="INIT", metadata={"session_id": sid})
            if stale:
                # The SDK reports a gone session: a §3.5 STALE_MARKER on an ERROR frame.
                # The runner surfaces this as ProviderError → resume_with_fallback's
                # replay arm rebuilds from history_fn and retries WITHOUT resume.
                yield AgentChunk(
                    type="ERROR",
                    metadata={"message": "No conversation found with session id sess-x"},
                )
                return
            yield AgentChunk(type="TEXT", text="continuing", metadata={"msg_id": "m1"})
            yield AgentChunk(
                type="RESULT",
                metadata={
                    "session_id": sid,
                    "num_turns": 1,
                    "total_cost_usd": 0.002,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 4000,
                    "input_tokens": 120,
                },
            )

        return gen()


class FakeSharedSandboxFS:
    """The ONE per-meeting warm sandbox filesystem (E2B ``files.read`` read-back)."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def write(self, path: str, data: bytes) -> None:
        self._files[path] = bytes(data)

    async def read_bytes(self, path: str) -> bytes | None:
        data = self._files.get(path)
        return None if data is None else bytes(data)


class FakeStore:
    """An ``operation_runs`` row store (§12.10) that also persists the SDK session id.

    Task durability IS the ``operation_runs`` row (progress = bundle, result_ref =
    Envelope) — there is NO ``workroom_tasks`` table. The persisted SDK session id
    rides the SAME row's ``progress`` jsonb (``progress['session_id']``) so a RESTART
    reads it back from the durable substrate and resumes (§3.1). ``set_session_id``
    models that persist; ``get_session_id`` models the restart read-back.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.tables_touched: set[str] = set()
        self.session_id_writes: list[tuple[str, str]] = []

    def claim(self, *, run_id: str, operation_type: str, progress: dict[str, Any]) -> None:
        self.tables_touched.add("operation_runs")
        self.rows[run_id] = {
            "id": run_id,
            "operation_type": operation_type,
            "status": "running",
            "progress": dict(progress),
            "result_ref": None,
        }

    async def set_session_id(self, *, run_id: str, session_id: str) -> None:
        self.tables_touched.add("operation_runs")
        self.session_id_writes.append((run_id, session_id))
        self.rows[run_id]["progress"]["session_id"] = session_id

    async def get_session_id(self, *, run_id: str) -> str | None:
        row = self.rows.get(run_id)
        if row is None:
            return None
        sid = row["progress"].get("session_id")
        return str(sid) if sid else None

    async def set_result(self, *, run_id: str, result_ref: dict[str, Any], status: str) -> None:
        self.tables_touched.add("operation_runs")
        row = self.rows[run_id]
        row["result_ref"] = dict(result_ref)
        row["status"] = status


def _bundle(meeting_id: uuid.UUID, ask: str, *, task_id: uuid.UUID | None = None) -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        notes_ref=meeting_id,
        transcript_tail=f"Sam: Proxy, {ask}.",
        task_id=task_id or uuid.uuid4(),
    )


def _dispatch(store: FakeStore, bundle: Bundle) -> str:
    from workroom.session import workroom_op_type

    run_id = uuid.uuid4().hex
    store.claim(
        run_id=run_id,
        operation_type=workroom_op_type(bundle.task_id),
        progress=bundle.model_dump(mode="json"),
    )
    return run_id


@pytest.fixture(autouse=True)
def _reset_provider_state() -> None:
    sandbox_provider._reset_for_test()
    yield
    sandbox_provider._reset_for_test()


def _provision(meeting_id: uuid.UUID) -> None:
    """Model the meeting-creation pre-provision that runs BEFORE a task runs/resumes.

    In production the meeting's ONE warm sandbox is pre-provisioned at meeting join (§3.9);
    a RESTART re-provisions it before the task resumes. The §3.9 preflight refuses to
    cold-boot mid-meeting, so the sandbox must be live for the meeting first — exactly the
    real ordering (pre-provision → run/resume)."""
    sandbox_provider.provision(meeting_id=str(meeting_id))


# ── NOT-done guards: the seams are IMPORTED from agentkit, never redefined ────


def test_resume_and_abort_are_imported_from_agentkit_never_redefined() -> None:
    """§11.9 invariant: ``resume_with_fallback`` and ``AbortRegistry`` are IMPORTED
    from ``agentkit`` and NEVER redefined in the session module (Doc 04 imports the
    SAME definitions). Asserted structurally: the module binds them by import, and its
    source defines no ``def resume_with_fallback`` / ``class AbortRegistry``."""
    import agentkit
    import workroom.session as mod

    # The names the module uses ARE the agentkit definitions (same object identity).
    assert mod.resume_with_fallback is agentkit.resume_with_fallback
    assert mod.AbortRegistry is agentkit.AbortRegistry

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            assert node.name != "resume_with_fallback", "resume_with_fallback must be IMPORTED, never redefined (§11.9)"
        if isinstance(node, ast.ClassDef):
            assert node.name != "AbortRegistry", "AbortRegistry must be IMPORTED, never redefined (§11.9)"
            assert node.name != "AbortController", "AbortController must be IMPORTED, never redefined (§11.9)"


# ── Tier-1: a killed-and-restarted task RESUMES its SDK conversation ──────────


def test_restarted_task_resumes_its_persisted_session_id() -> None:
    """DoD (Tier-1): a task runs and its SDK session id is PERSISTED per task; on a
    restart the driver reads it back and RESUMES the same conversation through the
    imported ``resume_with_fallback`` — the provider sees ``query.resume`` = the
    persisted id, and (the session being alive) NO history rebuild fires (§3.1)."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    store = FakeStore()

    # First run: a normal task. The SDK session id is captured + persisted per task.
    _provision(meeting_id)
    provider1 = FakeResumeProvider()
    driver1 = SessionDriver(provider=provider1, sandbox_fs=fs, store=store)
    b = _bundle(meeting_id, "build the retry refactor", task_id=task_id)
    run_id = _dispatch(store, b)
    env1 = asyncio.run(driver1.run_task(b, run_id=run_id))
    assert isinstance(env1, Envelope)
    # The session id was persisted into the SAME operation_runs row (durable substrate) —
    # §3.1's "persisted immediately, fire-and-forget, so a restart resumes". This write
    # happens BEFORE the terminal Envelope, so a mid-flight kill still leaves it durable.
    persisted = asyncio.run(store.get_session_id(run_id=run_id))
    assert persisted, "the SDK session id must be persisted per task so a restart can resume"
    assert store.session_id_writes, "the driver must persist the session id (§3.1 'persisted immediately')"

    # Model the KILL: the process died mid-flight, so the durable row is still ``running``
    # (the terminal Envelope was never written) — exactly the row a restart finds resumable.
    store.rows[run_id]["status"] = "running"

    # RESTART: a fresh driver (the process was killed). It resumes the SAME conversation.
    provider2 = FakeResumeProvider()
    driver2 = SessionDriver(provider=provider2, sandbox_fs=fs, store=store)
    env2 = asyncio.run(driver2.resume_task(b, run_id=run_id))

    assert isinstance(env2, Envelope), "the restarted task must produce a real Envelope"
    assert env2.task_id == task_id
    # Tier-1: the provider SAW the persisted session id on resume (same conversation).
    assert provider2.seen_resume[0] == persisted, "the restart must resume the persisted SDK session id"
    # The session being alive, NO stale-session replay fired: exactly one provider turn,
    # no bundle-derived preamble, no "restored" notice.
    assert provider2.calls == 1, "a live resume must not re-drive the provider (no fallback)"
    assert provider2.seen_preambles[0] is None, "a live resume must NOT rebuild history from the bundle"


# ── Tier-3: a FAILED resume rebuilds from the Bundle and CONTINUES ────────────


def test_failed_resume_rebuilds_from_bundle_and_continues() -> None:
    """DoD (Tier-3): when the resumed SDK session is GONE, the driver rebuilds context
    from the Bundle (``history_fn = rebuild_from_bundle``) and CONTINUES — a new session
    WITHOUT resume, carrying the bundle-derived preamble; the run still produces a real
    Envelope (§3.1 'on resume failure the context is rebuilt from the bundle')."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    store = FakeStore()

    # Persist a session id (as a prior run would), then restart into a GONE session.
    _provision(meeting_id)
    b = _bundle(meeting_id, "extend the checkout-retry refactor", task_id=task_id)
    run_id = _dispatch(store, b)
    asyncio.run(store.set_session_id(run_id=run_id, session_id="sess-dead"))

    provider = FakeResumeProvider(stale_on_resume=True)
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)
    env = asyncio.run(driver.resume_task(b, run_id=run_id))

    assert isinstance(env, Envelope), "a rebuilt-from-bundle run must still produce a real Envelope"
    assert env.task_id == task_id
    # The FIRST provider turn carried the (now-stale) resume id and went stale;
    # the replay retried a SECOND turn WITHOUT resume, carrying the bundle-derived preamble.
    assert provider.calls == 2, "a gone session must trigger exactly one bundle-rebuilt retry"
    assert provider.seen_resume[0] == "sess-dead", "the first turn must attempt the persisted resume"
    assert provider.seen_resume[1] is None, "the replay must retry WITHOUT resume (a new session)"
    preamble = provider.seen_preambles[1]
    assert preamble, "the replay must carry the bundle-derived history preamble"
    # The preamble is BUNDLE-derived (the node's history_fn = rebuild-from-bundle): it
    # carries the task's ask + transcript tail, not an empty/unrelated context.
    assert "extend the checkout-retry refactor" in preamble, "history_fn must rebuild from the BUNDLE (the ask)"
    assert "Proxy, extend the checkout-retry refactor" in preamble, "the bundle transcript tail must be in the rebuild"


def test_rebuild_from_bundle_is_bundle_derived_not_transcript_plane() -> None:
    """DoD (invariant / node risk): the Workroom's ``history_fn`` rebuilds from the
    BUNDLE (the ask + transcript tail the dispatch handed) — NOT Doc 04's transcript
    plane. A drifted history_fn would resume a different task; this pins it to the
    exact bundle the task was dispatched with (§3.1 'here history_fn = rebuild-from-bundle')."""
    from workroom.session import rebuild_from_bundle

    meeting_id = uuid.uuid4()
    b = _bundle(meeting_id, "trace the blast radius of the auth change", task_id=uuid.uuid4())
    history_fn = rebuild_from_bundle(b)

    history = asyncio.run(history_fn())
    text = history if isinstance(history, str) else "\n".join(str(h) for h in history)
    assert "trace the blast radius of the auth change" in text, "the rebuild must carry the bundle ask"
    assert b.transcript_tail in text, "the rebuild must carry the bundle transcript tail"
    assert str(b.speaker) in text, "the rebuild must name the asker (the bundle speaker)"


# ── abort is FINAL: an aborted task is NEVER resumed ─────────────────────────


def test_aborted_task_is_never_resumed() -> None:
    """DoD (abort is FINAL, §3.11): a task the user KILLED is NEVER resumed on restart —
    the provider is never driven, no stale-session replay resurrects it.

    The honest CROSS-RESTART signal is the DURABLE row: the abort flipped the
    ``operation_runs`` row to a TERMINAL status (the in-memory registry does not survive a
    process kill). A restart therefore only revives a row still ``running``; a terminal
    (aborted/failed) row is never resumed. The registry keyed ``meeting_id|task_id`` is the
    imported ``agentkit.AbortRegistry`` (never redefined) — its ``cancel`` also fired."""
    from agentkit import AbortRegistry

    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    store = FakeStore()

    _provision(meeting_id)
    b = _bundle(meeting_id, "build the thing the user killed", task_id=task_id)
    run_id = _dispatch(store, b)
    asyncio.run(store.set_session_id(run_id=run_id, session_id="sess-live"))

    # The user kills the task: the imported registry aborts+drops the controller AND the
    # abort handler flips the durable row to a terminal status (what survives the restart).
    registry = AbortRegistry()
    key = f"{meeting_id}|{task_id}"
    registry.make(key)
    registry.cancel(key)  # whisper-"stop" / meeting-end / timeout — abort + drop (§3.11)
    store.rows[run_id]["status"] = "aborted"  # the durable terminal record of the kill

    provider = FakeResumeProvider()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store, abort_registry=registry)
    env = asyncio.run(driver.resume_task(b, run_id=run_id))

    assert isinstance(env, Envelope), "an aborted resume must still return an honest Envelope (Rule 6)"
    # The build the user killed was NEVER resurrected: the provider was never driven.
    assert provider.calls == 0, "an ABORTED task must NEVER be resumed (§3.11 abort is FINAL)"
    assert not provider.seen_resume, "no resume attempt is allowed on an aborted task"
    # The Envelope honestly reports the task was aborted, not a false 'done'.
    assert env.status in {"failed", "partial"}, "an aborted task must not report a false success"
    # The durable row stays terminal — the resume never flipped it back to running/completed.
    assert store.rows[run_id]["status"] in {"aborted", "failed"}, "the killed row must stay terminal"


def test_completed_task_is_not_re_resumed() -> None:
    """DoD (abort-is-final's sibling guard): a task whose row is already TERMINAL (here a
    prior ``completed`` run) is NOT re-driven on a spurious restart — only a row still
    ``running`` is a genuine mid-flight kill worth resuming (§3.11 / §12.10). This is what
    stops a resume from resurrecting ANY finished build, not just an aborted one."""
    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    store = FakeStore()

    _provision(meeting_id)
    b = _bundle(meeting_id, "a task that already finished", task_id=task_id)
    run_id = _dispatch(store, b)
    asyncio.run(store.set_session_id(run_id=run_id, session_id="sess-done"))
    store.rows[run_id]["status"] = "completed"  # already terminal

    provider = FakeResumeProvider()
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store)
    env = asyncio.run(driver.resume_task(b, run_id=run_id))

    assert isinstance(env, Envelope)
    assert provider.calls == 0, "a terminal (completed) row must not be re-driven by a resume"


def test_abort_during_resume_is_final_no_replay() -> None:
    """DoD (abort is FINAL mid-resume, §3.11): if the controller fires WHILE a resume is
    running and the session then reports gone, the abort SHORT-CIRCUITS the stale-session
    replay — the run is not resurrected by the fallback. (The imported
    ``resume_with_fallback`` enforces this: a caller-abort is checked before recovery.)"""
    from agentkit import AbortRegistry

    from workroom.session import SessionDriver

    meeting_id = uuid.uuid4()
    task_id = uuid.uuid4()
    fs = FakeSharedSandboxFS()
    store = FakeStore()

    _provision(meeting_id)
    b = _bundle(meeting_id, "build then get killed mid-flight", task_id=task_id)
    run_id = _dispatch(store, b)
    asyncio.run(store.set_session_id(run_id=run_id, session_id="sess-dead"))

    registry = AbortRegistry()
    key = f"{meeting_id}|{task_id}"

    class _AbortingStaleProvider(FakeResumeProvider):
        """Fires the abort mid-stream, THEN reports the session gone — the replay must
        NOT resurrect it because a caller-abort is FINAL (§3.5/§3.11)."""

        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            self.calls += 1
            self.seen_resume.append(getattr(query, "resume", None))
            self.seen_prompts.append(prompt)
            self.seen_preambles.append(getattr(query, "preamble", None))

            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": "sess-x"})
                registry.cancel(key)  # the user kills it mid-flight (abort is FINAL)
                yield AgentChunk(
                    type="ERROR",
                    metadata={"message": "No conversation found with session id sess-dead"},
                )

            return gen()

    registry.make(key)
    provider = _AbortingStaleProvider(stale_on_resume=True)
    driver = SessionDriver(provider=provider, sandbox_fs=fs, store=store, abort_registry=registry)
    env = asyncio.run(driver.resume_task(b, run_id=run_id))

    assert isinstance(env, Envelope), "a mid-resume abort must still return an honest Envelope (Rule 6)"
    # The abort was FINAL: the stale-session replay did NOT fire a second, resume-less turn.
    assert provider.calls == 1, "a caller-abort must SHORT-CIRCUIT the stale-session replay (never resurrect)"
    assert env.status in {"failed", "partial"}, "an aborted-mid-resume task must not report a false success"
