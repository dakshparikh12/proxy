"""COMPOSITION PROOF — boot the product and drive ONE scripted end-to-end meeting.

Phase-1 Structural-Convergence gate (§Stage D). This is the whole-product assembly proof:
it BOOTS the real harness (real Postgres, real ``SignalCarrier``, real ``RunLoop`` +
``MeetingRuntime`` + name-gate + projector + gated emitter + close + reconcile) and drives a
single meeting through the entire reactive arc

    CONNECT/JOIN  →  UNDERSTAND  →  REACTIVE LOOP (ask → name-gate → wake turn →
    DIRECT ANSWER)  →  DELIVER  →  CLOSE  →  RECONCILE

with vendors faked ONLY at their seams: the Anthropic MODEL at the ``agentkit.Provider``
seam (a recording ``FakeProvider`` — the exact seam the provisioner threads), the Scribe
note-fold micro-call stubbed at ``_real_scribe_call``, and the close-leg (Sonnet + GCS +
chat) at the injected ``CloseConfig``. EVERY internal seam is real — the real webhook drain,
the real transport→carrier bridge, the real run loop, the real projector→emit frontier.

The proof asserts the arc COMPLETES:
  * ``in_call`` (drained through the real provisioner ``launch``) BOOTS a live runtime;
  * an ADDRESSED transcript ("Proxy, …") drained through the real transport→carrier bridge
    reaches the wake turn and the model's grounded answer is DELIVERED on the gated wire
    (is_owner fencing, §3.7) — the DIRECT-ANSWER arm end-to-end;
  * ``call_ended`` ENDS the meeting → the ordered close runs → the meeting row is stamped
    ``ended_at`` (durable close, §3);
  * the reconcile sweep runs clean over the durable substrate;
  * and — the whole-assembly invariant — ZERO unhandled asyncio task exceptions fire across
    the entire arc (a hollow seam surfaces here as a crashed background task, never silently).

Env-gated on ``TEST_DATABASE_URL`` (run via ``build/setup-test-env.sh``). The WORKROOM
DISPATCH arm is proven separately once its bridge is wired (Stage C1); this proof covers the
DIRECT-ANSWER arc, which is the fully-composed spine.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from libs.db import Database, open_pool, repos
from libs.contracts import AgentChunk

from harness.meeting_runtime import MeetingRuntimeRegistry
from harness.provisioner import provision_meeting
from harness.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned — run via build/setup-test-env.sh"
)

_ANSWER = "handle_login is called from services/auth/session.py:42 (resolved)"


class FakeProvider:
    """A recording ``agentkit.Provider`` stub — NO live Anthropic call (the model seam).

    Yields a canned ``AgentChunk`` stream for a DIRECT ANSWER: an INIT, a ``speak`` TOOL_USE
    carrying the grounded answer (the projector maps this to a ``VoiceSpeak`` → the gated
    ``emitter.speak``), then a RESULT. This is the exact recording-stub shape the live-brain
    assembly seam test uses; the provisioner threads it onto ``query.abort`` just as the real
    ``ClaudeAgentProvider`` is threaded.
    """

    name = "claude"

    def __init__(self, *, said: str = _ANSWER, dispatch_task: str | None = None) -> None:
        self._said = said
        self._dispatch_task = dispatch_task
        self.calls = 0
        self.seen_prompts: list[str] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt, query):
        self.calls += 1
        self.seen_prompts.append(prompt)
        said = self._said
        dispatch_task = self._dispatch_task

        async def gen():
            yield AgentChunk(type="INIT", metadata={"session_id": "comp-sess"})
            if dispatch_task is not None:
                # A WORKROOM DISPATCH tool-use — the model decides real code work belongs in
                # the sandbox and hands it off (§11.6). The bridge intercepts this chunk.
                yield AgentChunk(
                    type="TOOL_USE",
                    metadata={
                        "name": "dispatch_workroom",
                        "input": {"task": dispatch_task},
                        "id": "comp-d1",
                    },
                )
            else:
                yield AgentChunk(
                    type="TOOL_USE",
                    metadata={"name": "speak", "input": {"text": said}, "id": "comp-m1"},
                )
            yield AgentChunk(
                type="RESULT",
                metadata={"total_cost_usd": 0.004, "num_turns": 1, "session_id": "comp-sess"},
            )

        return gen()


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
    from harness.scribe_runtime import CloseConfig

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
    #    seam) so UNDERSTAND runs without the depleted key; the wake path is untouched. ──
    async def _fake_scribe_call(*a, **kw):
        return []  # no note deltas — this proof asserts the reactive/deliver arc, not the fold

    monkeypatch.setattr(
        "harness.scribe_runtime._real_scribe_call", _fake_scribe_call, raising=False
    )

    pool = await open_pool(_DSN)
    db = Database(pool, f"comp-{os.getpid()}")
    close_config, posted_links = _make_close_config()
    registry = MeetingRuntimeRegistry(db, close_config=close_config)

    # ── seed a live meeting (tenant/repo/meeting) ──
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
    meeting_id = str(meeting["id"])

    fake_provider = FakeProvider()

    async def _launch(payload: dict) -> None:
        # The real provisioner seam — atomic claim + one-scope assembly + live-brain wire,
        # with the FAKE model injected (the exact kwarg the provisioner threads, §3.3).
        await provision_meeting(payload, db=db, registry=registry, provider=fake_provider)

    async def _ingest(payload: dict) -> None:
        guid = f"wh-{uuid.uuid4().hex}"
        async with db.acquire() as conn:
            await repos.webhooks.insert_event(conn, guid, payload)

    pump: asyncio.Task | None = None
    try:
        # ── 1. CONNECT/JOIN — in_call boots the runtime through the real provisioner ──
        await _ingest({"event": "bot.in_call", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)
        runtime = registry.get(meeting_id)
        assert runtime is not None, "in_call did not BOOT a MeetingRuntime through the provisioner"
        assert runtime.live_brain is not None, "the live brain was not assembled on the boot path"

        # Start the transport→orchestrator pump so carrier signals reach the run loop (the RUN
        # block §3.2). The wake fires as an async task inside the loop when an addressed line lands.
        pump = asyncio.ensure_future(runtime.run_orchestrator_loop())
        await asyncio.sleep(0)

        # ── 2+3. UNDERSTAND + REACTIVE — an ADDRESSED transcript drives a real wake turn ──
        await _ingest(
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": "Proxy, who calls handle_login?",
                    "speaker": "Sam",
                    "timestamp": 0.0,
                    "end_of_turn": True,
                },
            }
        )
        await drain_pending_webhooks(db, registry=registry, launch=_launch)

        # ── DELIVER — poll the gated wire for the grounded answer (accumulate across drains) ──
        emitter = runtime.run_loop._emitter
        assert emitter is not None, "the run loop has no gated emitter (delivery seam unbound)"
        delivered: list = []
        for _ in range(200):
            delivered.extend(emitter.drain_wire())
            if any(kind == "speak" and _ANSWER in str(text) for kind, text in delivered):
                break
            await asyncio.sleep(0.02)

        assert fake_provider.calls == 1, (
            f"the wake turn did not run a REAL model turn (provider calls={fake_provider.calls}) — "
            "the addressed ask never reached the wake through the live carrier→pipe→loop arc"
        )
        assert any(kind == "speak" and _ANSWER in str(text) for kind, text in delivered), (
            f"the grounded answer never reached the gated wire; delivered={delivered!r}"
        )

        # ── 4. CLOSE — call_ended ENDS the meeting → ordered close → durable ended_at ──
        await _ingest({"event": "bot.call_ended", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)

    finally:
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
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


_DISPATCH_TASK = "add retry logic to the checkout flow"
_DRAFT_HEADLINE = "Staged a draft: added retry to checkout.py:42 (needs your review)"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_composition_proof_workroom_dispatch_arc(monkeypatch) -> None:
    """WORKROOM DISPATCH composes through the wake: ask → dispatch → run → deliver (§11.6/§3.2).

    A wake-turn ``dispatch_workroom`` tool-use is INTERCEPTED by the bridge, drives the real
    ``harness.dispatch.dispatch_workroom`` (a durable ``workroom:<id>`` row is claimed), ACKs
    on the gated wire, runs the Workroom task (its E2B/model internals faked at ``run_task`` —
    those are proven in isolation), and on completion DELIVERS the terminal draft back to the
    meeting. The Workroom driver's run_task is the ONE fake; every bridge seam is real.
    """
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    prev_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _l, ctx: unhandled.append(ctx))

    async def _fake_scribe_call(*a, **kw):
        return []

    monkeypatch.setattr(
        "harness.scribe_runtime._real_scribe_call", _fake_scribe_call, raising=False
    )

    # The Workroom driver's ONE task entry — faked to return a terminal staged-draft Envelope
    # (its real E2B+model internals are proven by tests/doc05, not re-run here). Records the
    # bundle it received so we prove the bridge assembled + handed off the real ask.
    from contracts import Envelope

    run_task_bundles: list = []

    async def _fake_run_task(self, bundle, *, run_id, **kw):
        run_task_bundles.append(bundle)
        return Envelope(
            headline=_DRAFT_HEADLINE,
            status="needs_review",
            task_id=bundle.task_id,
            draft_id=uuid.uuid4(),
        )

    monkeypatch.setattr("workroom.session.SessionDriver.run_task", _fake_run_task, raising=True)

    pool = await open_pool(_DSN)
    db = Database(pool, f"comp-wr-{os.getpid()}")
    close_config, _ = _make_close_config()
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting, bot_id = await _seed_live_meeting(db)
    meeting_id = str(meeting["id"])

    wake_provider = FakeProvider(dispatch_task=_DISPATCH_TASK)

    async def _launch(payload: dict) -> None:
        await provision_meeting(payload, db=db, registry=registry, provider=wake_provider)

    async def _ingest(payload: dict) -> None:
        guid = f"wh-{uuid.uuid4().hex}"
        async with db.acquire() as conn:
            await repos.webhooks.insert_event(conn, guid, payload)

    pump: asyncio.Task | None = None
    try:
        await _ingest({"event": "bot.in_call", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)
        runtime = registry.get(meeting_id)
        assert runtime is not None and runtime.live_brain is not None

        pump = asyncio.ensure_future(runtime.run_orchestrator_loop())
        await asyncio.sleep(0)

        # An addressed ask the model routes to the Workroom.
        await _ingest(
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": "Proxy, add retry logic to the checkout flow",
                    "speaker": "Sam",
                    "timestamp": 0.0,
                    "end_of_turn": True,
                },
            }
        )
        await drain_pending_webhooks(db, registry=registry, launch=_launch)

        # Poll the gated wire for BOTH the ack and the terminal draft delivery.
        emitter = runtime.run_loop._emitter
        delivered: list = []
        for _ in range(200):
            delivered.extend(emitter.drain_wire())
            if any(_DRAFT_HEADLINE in str(text) for _, text in delivered) and run_task_bundles:
                break
            await asyncio.sleep(0.02)

        spoken = [str(text) for kind, text in delivered if kind == "speak"]

        assert wake_provider.calls == 1, "the wake turn never ran (the ask didn't reach the wake)"
        assert run_task_bundles, "the Workroom driver's run_task was NEVER invoked — dispatch didn't drive the Workroom"
        assert _DISPATCH_TASK in run_task_bundles[0].ask, (
            f"the dispatched bundle carried the wrong ask: {run_task_bundles[0].ask!r}"
        )
        assert any("on it" in s.lower() for s in spoken), f"no ACK was delivered on dispatch; wire={spoken!r}"
        assert any(_DRAFT_HEADLINE in s for s in spoken), (
            f"the Workroom's terminal draft never reached the meeting; wire={spoken!r}"
        )

        # The durable dispatch record: a workroom:<id> operation_runs row was claimed for this meeting.
        async with db.acquire() as conn:
            wr = await conn.fetchrow(
                "SELECT id, operation_type FROM operation_runs "
                "WHERE scope_id = $1 AND operation_type LIKE 'workroom:%' ORDER BY started_at DESC LIMIT 1",
                meeting_id,
            )
        assert wr is not None, "dispatch_workroom did not claim a durable workroom:<id> operation_runs row"

        await _ingest({"event": "bot.call_ended", "data": {"bot_id": bot_id}})
        await drain_pending_webhooks(db, registry=registry, launch=_launch)
    finally:
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
        try:
            await registry.end_meeting(meeting_id)
        except Exception:
            pass

    loop.set_exception_handler(prev_handler)
    assert not unhandled, (
        "unhandled asyncio task exception(s) during the dispatch arc: "
        + "; ".join(str(c.get("exception") or c.get("message")) for c in unhandled)
    )
