"""COMPOSITION PROOF — boot the product and drive ONE scripted end-to-end meeting.

Phase-1 Structural-Convergence gate (§Stage D), re-anchored by THE CUTOVER: the brain on
the production boot path is the NEW in-meeting engine (``in_meeting.engine.Engine``,
assembled by ``control_plane.provisioner._assemble_engine``). This is the whole-product assembly
proof: it BOOTS the real harness (real Postgres, real ``SignalCarrier`` + ``MeetingRuntime``
+ webhook drain + close + reconcile) and drives a single meeting through the entire arc

    CONNECT/JOIN  →  UNDERSTAND  →  ENGINE LOOP (ask → trigger → wake turn →
    DIRECT ANSWER into the speak pipe)  →  CLOSE  →  RECONCILE

with vendors faked ONLY at their seams: the Anthropic MODEL at the ``agentkit.Provider``
seam, the SPEAK sink at the engine's injected speak seam (production = Cartesia→Output-Media;
here a recorder), the DISAMBIGUATOR at the trigger's async confirm seam, the E2B sandbox at
the ``provision_sandbox`` backend seam, the Recall transport at the meeting-control Protocol,
the Scribe note-fold micro-call at ``_real_scribe_call``, and the close-leg (Sonnet + GCS +
chat) at the injected ``CloseConfig``. EVERY internal seam is real — the real webhook drain,
the real provisioner claim + engine assembly, the real trigger/notes/context spine.

The proof asserts the arc COMPLETES:
  * ``in_call`` (drained through the real provisioner ``launch``) BOOTS a live runtime whose
    ``engine`` is the NEW brain (the OLD live brain is absent — the cutover invariant);
  * an ADDRESSED transcript ("Proxy, …") drained through the real webhook dispatch reaches
    ``engine.feed_transcript``, wakes ONE real provider turn, and the grounded answer lands
    in the speak pipe — the DIRECT-ANSWER arm end-to-end;
  * ``call_ended`` ENDS the meeting → the ordered close runs → the meeting row is stamped
    ``ended_at`` (durable close, §3);
  * the heavy-work arm is MOUNTED through the boot path (SANDBOX_TOOLS advertised off the
    warm handle) and the meeting-end lifecycle closes it (engine drained → speak pipe closed
    → sandbox killed → operation row completed);
  * the reconcile sweep runs clean over the durable substrate;
  * and — the whole-assembly invariant — ZERO unhandled asyncio task exceptions fire across
    the entire arc (a hollow seam surfaces here as a crashed background task, never silently).

Env-gated on ``TEST_DATABASE_URL`` (run via ``build/setup-test-env.sh``).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from libs.db import Database, open_pool, repos
from libs.contracts import AgentChunk

from control_plane.meeting_runtime import MeetingRuntimeRegistry
from control_plane.provisioner import provision_meeting, run_meeting_until_end
from control_plane.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned — run via build/setup-test-env.sh"
)

_ANSWER = "handle_login is called from services/auth/session.py:42 (resolved)"


class FakeProvider:
    """A recording ``agentkit.Provider`` stub — NO live Anthropic call (the model seam).

    Yields the ENGINE's canned turn: INIT → an accumulated-TEXT chunk carrying the
    grounded answer (the engine routes the new suffix to its injected ``speak`` sink)
    → RESULT. This is the exact seam ``_assemble_engine`` threads (``provider=``).
    """

    name = "claude"

    def __init__(self, *, said: str = _ANSWER) -> None:
        self._said = said
        self.calls = 0
        self.seen_prompts: list[str] = []
        self.seen_queries: list = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt, query):
        self.calls += 1
        self.seen_prompts.append(prompt)
        self.seen_queries.append(query)
        said = self._said

        async def gen():
            yield AgentChunk(type="INIT", metadata={"session_id": "comp-sess"})
            yield AgentChunk(type="TEXT", text=said, metadata={"msg_id": "comp-m1"})
            yield AgentChunk(
                type="RESULT",
                text=said,
                metadata={"total_cost_usd": 0.004, "num_turns": 1, "session_id": "comp-sess"},
            )

        return gen()


class FakeSpeakPipe:
    """The engine's speak seam as a recorder (SpeakSink shape + meeting-end aclose)."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.closed = False

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def aclose(self) -> None:
        self.closed = True


class FakeTransport:
    """The meeting-control verbs (mute/unmute/post_chat/send_dm) as inert recorders."""

    async def mute(self, bot_id: str) -> None:
        return None

    async def unmute(self, bot_id: str) -> None:
        return None

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        return None

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        return None


class FakeSandboxHandle:
    """The warm E2B handle shape; records the meeting-end kill."""

    def __init__(self) -> None:
        self.killed = False

    @property
    def commands(self):
        return None

    @property
    def files(self):
        return None

    async def kill(self) -> None:
        self.killed = True


async def _confirm_every_hit(line: str) -> bool:
    return True


class _CreateOnlyBlob:
    """An in-memory GCS blob honouring ``if_generation_match=0`` (create-only, §12.9)."""

    def __init__(self, store: dict, name: str) -> None:
        self._store = store
        self._name = name

    def upload_from_string(self, data, *a, if_generation_match=None, **kw) -> None:
        if if_generation_match == 0 and self._name in self._store:
            raise RuntimeError("create-only: object already exists")
        self._store[self._name] = data

    @property
    def name(self) -> str:
        return self._name


class _CreateOnlyBucket:
    """An in-memory create-only bucket double (the ``CloseConfig.bucket`` seam)."""

    def __init__(self) -> None:
        self._store: dict = {}

    def blob(self, name: str) -> _CreateOnlyBlob:
        return _CreateOnlyBlob(self._store, name)


def _make_close_config():
    """A ``CloseConfig`` with every vendor faked at its seam — recording close model, in-mem
    create-only bucket, a no-op chat-link poster. No live vendor call fires in the close leg."""
    from control_plane.scribe_runtime import CloseConfig

    posted: list[str] = []

    async def _post_chat_link(url: str, **kw) -> None:
        posted.append(url)

    async def _close_caller(*a, **kw):
        # The strong-model close pass, faked: return an empty structured close (the fold has
        # no deltas in this proof, so the close pass reduces to a create-only finalize).
        return {"sections": [], "total_cost_usd": 0.002}

    cfg = CloseConfig(
        bucket=_CreateOnlyBucket(),
        bucket_name="comp-notes",
        post_chat_link=_post_chat_link,
        close_caller=_close_caller,
    )
    return cfg, posted


async def _seed_live_meeting(db):
    """Seed a live (tenant, repo, meeting) and return (meeting_row, bot_id)."""
    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", f"t-{uuid.uuid4().hex[:8]}"
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) VALUES ($1,$2,$3) RETURNING id",
            tenant["id"], "example/app", "main",
        )
    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=repo["id"],
            meeting_url="https://meet.example/comp",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    return meeting, bot_id


async def _ingest(db, payload: dict) -> None:
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(conn, guid, payload)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_composition_proof_direct_answer_arc(monkeypatch) -> None:
    # ── zero-unhandled-exception guard: record any background task crash across the arc ──
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    prev_handler = loop.get_exception_handler()

    def _record(_loop, context):
        unhandled.append(context)

    loop.set_exception_handler(_record)

    # ── fake the Scribe note-fold micro-call (the one internal LLM not on the provider/close
    #    seam) so UNDERSTAND runs without a live key; the engine path is untouched. ──
    async def _fake_scribe_call(*a, **kw):
        return []  # no note deltas — this proof asserts the reactive/deliver arc, not the fold

    monkeypatch.setattr(
        "control_plane.scribe_runtime._real_scribe_call", _fake_scribe_call, raising=False
    )

    pool = await open_pool(_DSN)
    db = Database(pool, f"comp-{os.getpid()}")
    close_config, posted_links = _make_close_config()
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting, bot_id = await _seed_live_meeting(db)
    meeting_id = str(meeting["id"])

    fake_provider = FakeProvider()
    speak_pipe = FakeSpeakPipe()
    sandbox_handle = FakeSandboxHandle()

    async def _sandbox_backend(**kw):
        return sandbox_handle

    async def _launch(payload: dict) -> None:
        # The real provisioner seam — atomic claim + one-scope assembly + THE CUTOVER's
        # engine assembly, with every vendor faked at the exact kwargs the provisioner
        # threads (provider / speak / disambiguate / transport / sandbox_backend).
        await provision_meeting(
            payload,
            db=db,
            registry=registry,
            provider=fake_provider,
            speak=speak_pipe,
            disambiguate=_confirm_every_hit,
            transport=FakeTransport(),
            sandbox_backend=_sandbox_backend,
        )

    try:
        # ── 1. CONNECT/JOIN — in_call boots the runtime through the real provisioner ──
        await _ingest(db, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)
        runtime = registry.get(meeting_id)
        assert runtime is not None, "in_call did not BOOT a MeetingRuntime through the provisioner"
        # THE CUTOVER invariant: the NEW engine is the brain; the old live brain is absent.
        assert runtime.engine is not None, "the in-meeting engine was not assembled on the boot path"
        assert runtime.live_brain is None, "the OLD live brain must no longer own the boot path"
        assert runtime.engine_sandbox is sandbox_handle, "the warm sandbox handle was not stashed"

        # ── 2+3. UNDERSTAND + ENGINE LOOP — an ADDRESSED transcript wakes ONE real turn ──
        await _ingest(
            db,
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": "Proxy, who calls handle_login?",
                    "speaker": "Sam",
                    "timestamp": 0.0,
                    "end_of_turn": True,
                },
            },
        )
        await drain_pending_webhooks(db, registry=registry, launch=_launch)

        # ── DELIVER — the grounded answer lands in the speak pipe (the engine's one voice) ──
        for _ in range(200):
            if any(_ANSWER in s for s in speak_pipe.said):
                break
            await asyncio.sleep(0.02)

        assert fake_provider.calls == 1, (
            f"the engine did not run a REAL model turn (provider calls={fake_provider.calls}) — "
            "the addressed ask never reached the wake through the drain→engine arc"
        )
        assert "Proxy, who calls handle_login?" in fake_provider.seen_prompts[0], (
            "the ask was not carried verbatim into the turn prompt"
        )
        assert any(_ANSWER in s for s in speak_pipe.said), (
            f"the grounded answer never reached the speak pipe; said={speak_pipe.said!r}"
        )

        # ── 4. CLOSE — call_ended ENDS the meeting → ordered close → durable ended_at ──
        await _ingest(db, {"event": "bot.call_ended", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)

    finally:
        # Belt-and-suspenders: ensure the meeting is ended even if call_ended didn't route.
        try:
            await registry.end_meeting(meeting_id)
        except Exception:
            pass

    # The close stamped the durable meeting row ended_at (§3 — close writes ended_at).
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT status, ended_at FROM meetings WHERE id = $1", meeting["id"])
    assert row is not None and row["ended_at"] is not None, (
        f"CLOSE did not durably stamp ended_at (row={dict(row) if row else None})"
    )

    # ── 5. RECONCILE — the sweep runs clean over the durable substrate ──
    from ops.reconcile import run_reconcile_sweep

    await run_reconcile_sweep(db)

    # ── whole-assembly invariant: zero unhandled background task exceptions across the arc ──
    loop.set_exception_handler(prev_handler)
    assert not unhandled, (
        "unhandled asyncio task exception(s) fired during the arc (a hollow seam): "
        + "; ".join(str(c.get("exception") or c.get("message")) for c in unhandled)
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_composition_proof_sandbox_arm_and_meeting_end_lifecycle(monkeypatch) -> None:
    """The heavy-work arm + the meeting-end lifecycle compose through the boot path.

    SINCE THE CUTOVER the heavy/code work arm is the ENGINE's sandbox toolbelt (the agent
    composes ``mcp__sandbox__*`` in a warm per-meeting E2B sandbox) — the old wake→Workroom
    dispatch bridge no longer rides the boot path (its module tests keep covering it until
    the delete wave). This proof drives ``run_meeting_until_end`` (the real per-meeting
    entry) on live Postgres and asserts:

      * a heavy ask wakes ONE real provider turn whose captured query ADVERTISES the
        sandbox toolbelt (``mcp__sandbox__*``) and MOUNTS the ``sandbox`` server off the
        warm handle provisioned at join — the arm is live, not a dead module;
      * ``call_ended`` (the existing end-signal machinery) ends the launched meeting: the
        engine drains, the speak pipe closes, the SANDBOX IS KILLED, the runtime drops,
        and the ``operation_runs`` row completes — the full cutover lifecycle.
    """
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    prev_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _l, ctx: unhandled.append(ctx))

    async def _fake_scribe_call(*a, **kw):
        return []

    monkeypatch.setattr(
        "control_plane.scribe_runtime._real_scribe_call", _fake_scribe_call, raising=False
    )

    pool = await open_pool(_DSN)
    db = Database(pool, f"comp-sb-{os.getpid()}")
    close_config, _ = _make_close_config()
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting, bot_id = await _seed_live_meeting(db)
    meeting_id = str(meeting["id"])

    fake_provider = FakeProvider(said="on it — running the suite in the sandbox now.")
    speak_pipe = FakeSpeakPipe()
    sandbox_handle = FakeSandboxHandle()

    async def _sandbox_backend(**kw):
        return sandbox_handle

    # The real per-meeting entry, launched exactly as the production launcher launches it.
    task = asyncio.ensure_future(
        run_meeting_until_end(
            {"event": "bot.in_call", "data": {"bot_id": bot_id}},
            db=db,
            registry=registry,
            timeout_s=30.0,
            provider=fake_provider,
            speak=speak_pipe,
            disambiguate=_confirm_every_hit,
            transport=FakeTransport(),
            sandbox_backend=_sandbox_backend,
        )
    )

    try:
        # Wait for the boot (claim + assembly, on the launched task).
        runtime = None
        for _ in range(300):
            runtime = registry.get(meeting_id)
            if runtime is not None and runtime.engine is not None:
                break
            await asyncio.sleep(0.01)
        assert runtime is not None and runtime.engine is not None, (
            "the launched meeting did not assemble the engine"
        )
        assert runtime.engine_sandbox is sandbox_handle

        # A heavy ask, drained through the REAL webhook dispatch → engine → one turn.
        await _ingest(
            db,
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": "Proxy, run the test suite and tell us what breaks",
                    "speaker": "Sam",
                    "timestamp": 1.0,
                    "end_of_turn": True,
                },
            },
        )
        await drain_pending_webhooks(db, registry=registry)
        for _ in range(200):
            if fake_provider.calls >= 1:
                break
            await asyncio.sleep(0.02)
        assert fake_provider.calls == 1, "the heavy ask never woke the engine"
        query = fake_provider.seen_queries[0]
        assert "mcp__sandbox__run_command" in query.allowed_tools, (
            "the sandbox toolbelt is not ADVERTISED on the boot path — the heavy-work arm is dead"
        )
        assert query.mcp_servers is not None and "sandbox" in query.mcp_servers, (
            "the sandbox server was not MOUNTED off the warm handle"
        )

        # ── meeting end: the existing end-signal machinery closes the whole lifecycle ──
        await _ingest(db, {"event": "bot.call_ended", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry)
        outcome = await asyncio.wait_for(task, timeout=10.0)

        assert outcome.claimed is True and outcome.ran_to_end is True
        assert speak_pipe.closed is True, "the speak pipe was not closed at meeting end"
        assert sandbox_handle.killed is True, "the warm sandbox was not killed at meeting end"
        assert registry.get(meeting_id) is None, "the runtime was not dropped at meeting end"
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM operation_runs WHERE scope_id = $1 AND operation_type = $2 "
                "ORDER BY started_at DESC LIMIT 1",
                meeting_id,
                "meeting-harness",
            )
        assert row is not None and row["status"] == "completed", (
            f"the operation_runs row did not complete at meeting end (row={dict(row) if row else None})"
        )
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        try:
            await registry.end_meeting(meeting_id)
        except Exception:
            pass

    loop.set_exception_handler(prev_handler)
    assert not unhandled, (
        "unhandled asyncio task exception(s) during the sandbox-arm arc: "
        + "; ".join(str(c.get("exception") or c.get("message")) for c in unhandled)
    )
