"""B6 — dispatch. Criteria: AC-PME-09/-NEG, AC-PME-10/-NEG, AC-PME-11/-NEG, AC-PME-12/-NEG."""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from harness.post_meeting.approval import approve
from harness.post_meeting.config import PostMeetingConfig
from harness.post_meeting.dispatch import (
    DispatchDecision,
    check_caps,
    post_meeting_worker,
    run_dispatch,
)
from harness.post_meeting.models import Source, TaskRecord, TaskState

from ._support import FakeTaskStore, ForbiddenSandbox

pytestmark = pytest.mark.asyncio

TENANT = uuid.uuid4()
MEETING = uuid.uuid4()
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
CFG = PostMeetingConfig(max_concurrent_tasks=2, max_tasks_per_meeting=3, task_cost_ceiling=1.0)


class FakeBundle:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def fake_assemble(**kw):
    assert set(kw) >= {"ask", "speaker", "timestamp", "meeting_id", "task_id"}
    return FakeBundle(**kw)


class RecordingWorkroom:
    """Counts dispatches. The ONLY execution path B6 may reach."""

    def __init__(self, envelope=None, error=None):
        self.calls: list[object] = []
        self._envelope = envelope or {"status": "done"}
        self._error = error

    async def __call__(self, bundle):
        self.calls.append(bundle)
        if self._error:
            raise self._error
        return self._envelope


async def _approved(store, owner="Sam"):
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner=owner)
    )
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver=owner, store=store, now=NOW)
    return tid


async def _dispatch(store, wr, **over):
    kw = dict(
        task_id=over.pop("task_id"), tenant_id=TENANT, meeting_id=MEETING,
        ask="bump the retry ceiling", speaker="Sam", timestamp=NOW,
        store=store, workroom_dispatch=wr, assemble_bundle=fake_assemble, config=CFG,
    )
    kw.update(over)
    return await run_dispatch(**kw)


# ── AC-PME-09 · Doc 04's bundle, Doc 05's Workroom, no second path ────────
async def test_ac_pme_09_dispatch_uses_the_canonical_bundle_and_workroom():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)
    assert out.dispatched
    assert len(wr.calls) == 1
    b = wr.calls[0]
    assert b.meeting_id == MEETING and b.task_id == tid


async def test_ac_pme_09_no_second_execution_path_exists_in_the_module():
    """Static: B6 imports no provider, queue, scheduler or broker of its own."""
    src = pathlib.Path(
        "services/harness/src/harness/post_meeting/dispatch.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    banned = ("celery", "rq", "kombu", "kafka", "pika", "apscheduler", "e2b", "boto3")
    for mod in imported:
        assert not any(b in (mod or "").lower() for b in banned), f"second engine: {mod}"
    lowered = src.lower()
    for token in ("class .*queue", "sandboxprovider(", "e2b.", "scheduler("):
        assert token not in lowered


async def test_ac_pme_09_whole_package_introduces_no_sandbox_provider():
    pkg = pathlib.Path("services/harness/src/harness/post_meeting")
    for path in pkg.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "e2b" not in text or "sandbox" not in text.split("e2b")[0][-40:], path
        assert "import celery" not in text and "import rq" not in text, path


# ── AC-PME-09-NEG · a dispatch failure never uses a fallback path ─────────
@pytest.mark.negative
async def test_ac_pme_09_neg_dispatch_failure_uses_no_fallback_runner():
    store = FakeTaskStore()
    wr = RecordingWorkroom(error=ConnectionResetError("workroom unreachable"))
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)

    assert out.decision is DispatchDecision.ERROR
    assert isinstance(out.error, ConnectionResetError)
    assert len(wr.calls) == 1, "the workroom was the only path attempted"
    assert "no fallback path exists" in out.detail
    assert out.dispatched is False


@pytest.mark.negative
async def test_ac_pme_09_neg_failure_does_not_report_progress():
    store = FakeTaskStore()
    wr = RecordingWorkroom(error=RuntimeError("boom"))
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)
    assert out.envelope is None, "a failed dispatch must not carry a result envelope"


# ── AC-PME-10 · exactly one operation_runs row; lifecycle never mirrors it ─
async def test_ac_pme_10_task_record_carries_no_run_state_columns():
    """post_meeting_tasks is lifecycle state, never a second run table (D07.1)."""
    store = FakeTaskStore()
    tid = await _approved(store)
    row = store.rows[tid]
    for forbidden in ("status", "progress", "last_heartbeat_at", "heartbeat"):
        assert forbidden not in row, f"post_meeting_tasks mirrors the run via {forbidden!r}"
    assert "operation_ref" in row, "the run is referenced, not duplicated"


async def test_ac_pme_10_operation_ref_points_at_one_run():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)
    assert out.operation_ref == tid
    assert store.rows[tid]["operation_ref"] == tid


async def test_ac_pme_10_migration_keys_the_run_by_meeting_and_task():
    """Amendment P10: scope_id = meeting_id, operation_type = 'workroom:{task_id}'."""
    src = pathlib.Path(
        "services/harness/src/harness/dispatch.py"
    ).read_text(encoding="utf-8")
    assert 'WORKROOM_OP_PREFIX = "workroom:"' in src
    assert "scope_id is the sole text column" in src


# ── AC-PME-10-NEG · concurrent claims yield one row ───────────────────────
@pytest.mark.negative
async def test_ac_pme_10_neg_two_dispatches_of_one_task_do_not_double_run():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)
    first = await _dispatch(store, wr, task_id=tid)
    second = await _dispatch(store, wr, task_id=tid)

    assert first.dispatched
    # The task is RUNNING now, not APPROVED, so the gate refuses the second attempt.
    assert second.decision is DispatchDecision.NOT_APPROVED
    assert len(wr.calls) == 1, "the same task was dispatched twice"


@pytest.mark.negative
async def test_ac_pme_10_neg_the_index_not_an_app_lock_is_the_excluder():
    """The claim is an INSERT ... ON CONFLICT against the partial unique index."""
    src = pathlib.Path("libs/ops/src/ops/claim.py").read_text(encoding="utf-8")
    assert "ON CONFLICT (scope_id, operation_type) WHERE status = 'running'" in src
    assert "threading.Lock" not in src and "asyncio.Lock" not in src


# ── AC-PME-11 · caps make dispatch wait, never drop ───────────────────────
async def test_ac_pme_11_concurrency_cap_holds_dispatch():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    for _ in range(CFG.max_concurrent_tasks):
        t = await _approved(store)
        await store.set_state(t, TaskState.RUNNING)
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)

    assert out.decision is DispatchDecision.WAITING_CONCURRENCY
    assert out.waiting is True, "a capped task must WAIT, not be dropped"
    assert wr.calls == []


async def test_ac_pme_11_task_dispatches_once_a_slot_frees():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    blockers = []
    for _ in range(CFG.max_concurrent_tasks):
        t = await _approved(store)
        await store.set_state(t, TaskState.RUNNING)
        blockers.append(t)
    tid = await _approved(store)
    assert (await _dispatch(store, wr, task_id=tid)).waiting

    await store.set_outcome(blockers[0], state=TaskState.DRAFTED, outcome="done")
    out = await _dispatch(store, wr, task_id=tid)
    assert out.dispatched, "the held task never became dispatchable"


async def test_ac_pme_11_concurrency_never_exceeds_the_cap():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    observed = []
    for _ in range(6):
        tid = await _approved(store)
        out = await _dispatch(store, wr, task_id=tid)
        if out.dispatched:
            observed.append(await store.count_running_for_tenant(TENANT))
    assert max(observed) <= CFG.max_concurrent_tasks


# ── AC-PME-11-NEG · an unreadable count is not zero ───────────────────────
@pytest.mark.negative
async def test_ac_pme_11_neg_unreadable_count_holds_rather_than_admits():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)

    async def boom(_tenant):
        raise ConnectionResetError("count query failed")

    store.count_running_for_tenant = boom  # type: ignore[method-assign]
    out = await _dispatch(store, wr, task_id=tid)

    assert out.decision is DispatchDecision.WAITING_CONCURRENCY
    assert wr.calls == [], "dispatch was admitted on an unreadable count"
    assert "unreadable" in out.detail


@pytest.mark.negative
async def test_ac_pme_11_neg_cap_check_reads_live_counts_not_a_local_counter():
    store = FakeTaskStore()
    for _ in range(CFG.max_concurrent_tasks):
        t = await _approved(store)
        await store.set_state(t, TaskState.RUNNING)
    assert await check_caps(
        tenant_id=TENANT, meeting_id=MEETING, store=store, config=CFG
    ) is DispatchDecision.WAITING_CONCURRENCY


# ── AC-PME-12 · cost ask happens before the sandbox spins ─────────────────
async def test_ac_pme_12_over_ceiling_asks_the_owner_and_starts_nothing():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    sandbox = ForbiddenSandbox()
    tid = await _approved(store)

    async def estimate(_t):
        return 5.00

    out = await _dispatch(store, wr, task_id=tid, estimate_cost=estimate)
    assert out.decision is DispatchDecision.COST_ASK
    assert out.estimated_cost_usd == 5.00
    assert wr.calls == [] and sandbox.call_count == 0
    assert store.rows[tid]["cost_usd"] == 0.0, "spend began before the owner answered"


async def test_ac_pme_12_under_ceiling_dispatches():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)

    async def estimate(_t):
        return 0.25

    out = await _dispatch(store, wr, task_id=tid, estimate_cost=estimate)
    assert out.dispatched and len(wr.calls) == 1


async def test_ac_pme_12_answered_cost_ask_proceeds():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)

    async def estimate(_t):
        return 99.0

    out = await _dispatch(
        store, wr, task_id=tid, estimate_cost=estimate, cost_answered=True
    )
    assert out.dispatched, "an owner-answered cost ask must be able to proceed"


# ── AC-PME-12-NEG · unavailable estimate is never treated as cheap ────────
@pytest.mark.negative
async def test_ac_pme_12_neg_unavailable_estimate_blocks_the_sandbox():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    sandbox = ForbiddenSandbox()
    tid = await _approved(store)

    async def broken(_t):
        raise ConnectionResetError("meeting_cost read failed")

    out = await _dispatch(store, wr, task_id=tid, estimate_cost=broken)
    assert out.decision is DispatchDecision.COST_ASK
    assert wr.calls == [] and sandbox.call_count == 0
    assert store.rows[tid]["cost_usd"] == 0.0


@pytest.mark.negative
async def test_ac_pme_12_neg_unapproved_task_never_reaches_cost_or_workroom():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner="Sam")
    )
    asked = {"n": 0}

    async def estimate(_t):
        asked["n"] += 1
        return 0.1

    out = await _dispatch(store, wr, task_id=tid, estimate_cost=estimate)
    assert out.decision is DispatchDecision.NOT_APPROVED
    assert asked["n"] == 0, "cost was estimated for an unapproved task"
    assert wr.calls == []


# ── the no-media worker (Doc 07 §3.5) ─────────────────────────────────────
async def test_no_media_worker_constructs_nothing_media_bearing():
    rt = post_meeting_worker(header=object(), carrier=object(), db=object(), host_budget=object())
    assert rt.media_session is False
    assert rt.consent_gate is None, "a no-media worker must not build a consent gate"
    assert rt._scribe is None and rt._hearing is None
    assert rt.stt_refresh_running is False


async def test_no_media_worker_refuses_to_become_an_observing_one():
    rt = post_meeting_worker(header=object(), carrier=object(), db=object(), host_budget=object())
    with pytest.raises(RuntimeError, match="no-media meeting_runtime"):
        rt.start()
    with pytest.raises(RuntimeError, match="no-media meeting_runtime"):
        await rt.ingest_transcript({"words": "hello"})


async def test_no_media_worker_is_the_same_class_not_a_new_deployable():
    from harness.meeting_runtime import MeetingRuntime

    rt = post_meeting_worker(header=object(), carrier=object(), db=object(), host_budget=object())
    assert isinstance(rt, MeetingRuntime)
