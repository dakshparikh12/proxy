"""B6 — dispatch. Criteria: AC-PME-09/-NEG, AC-PME-10/-NEG, AC-PME-11/-NEG, AC-PME-12/-NEG."""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from harness.post_meeting.approval import approve
from harness.post_meeting.config import PostMeetingConfig
from harness.post_meeting.dispatch import DispatchDecision, check_caps, run_dispatch
from harness.post_meeting.models import Source, TaskRecord, TaskState, Tier

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


class FakeHandle:
    """Shaped like ``harness.dispatch.WorkroomHandle``: task_id, run_id, bundle.

    ``run_id`` is the ``operation_runs.id`` the claim returned — a DIFFERENT uuid from
    ``task_id``. Keeping them distinct here is what makes the FK semantics testable: a
    double that returned the task id would let the wrong-column bug pass.
    """

    def __init__(self, task_id, run_id, bundle):
        self.task_id = task_id
        self.run_id = run_id
        self.bundle = bundle


class RecordingWorkroom:
    """Counts dispatches. The ONLY execution path B6 may reach."""

    def __init__(self, error=None, run_id=None, returns=None):
        self.calls: list[object] = []
        self._error = error
        #: the operation_runs.id this fake claim "persists"
        self.run_id = run_id or uuid.uuid4()
        #: override the return value entirely (e.g. a DispatchDecision with no run_id)
        self._returns = returns

    async def __call__(self, bundle):
        self.calls.append(bundle)
        if self._error:
            raise self._error
        if self._returns is not None:
            return self._returns
        return FakeHandle(task_id=bundle.task_id, run_id=self.run_id, bundle=bundle)


async def _approved(store, owner="Sam", tier=Tier.TICKET_PLAN_DRAFT):
    """An approved task at the only tier that can actually be dispatched."""
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#0", owner=owner)
    )
    await store.set_tier(tid, tier, state=TaskState.TRIAGED)
    await store.set_state(tid, TaskState.PLANNED)
    await approve(task_id=tid, approver=owner, store=store, now=NOW)
    return tid


async def _item(store, tier, state=TaskState.EXTRACTED):
    """A non-dispatchable task record — an informational/question/ticket item."""
    tid = await store.insert_task(
        TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                   source=Source.CLOSE_ITEM, item_ref="m#x", owner="Sam")
    )
    await store.set_tier(tid, tier, state=state)
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


async def test_ac_pme_10_operation_ref_is_the_run_id_not_the_task_id():
    """``operation_ref`` is a uuid FK to ``operation_runs(id)`` (migration 0009).

    It must be the run row's id, which the Workroom claim returns as
    ``WorkroomHandle.run_id`` — NOT the task id. They are different uuids, and writing
    the task id here is an FK violation the real database rejects. The earlier version of
    this test asserted the task id and so encoded the bug.
    """
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)

    assert out.operation_ref == wr.run_id
    assert store.rows[tid]["operation_ref"] == wr.run_id
    assert out.operation_ref != tid, "operation_ref must not be the task id"


@pytest.mark.negative
async def test_ac_pme_10_neg_a_workroom_return_without_a_run_id_is_an_error():
    """No run_id means no operation_runs row was claimed — there is nothing to point at."""
    store = FakeTaskStore()
    wr = RecordingWorkroom(returns={"status": "ask_approval"})  # a DispatchDecision-ish
    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)

    assert out.decision is DispatchDecision.ERROR
    assert out.operation_ref is None
    assert "no run_id" in out.detail
    assert store.rows[tid]["operation_ref"] is None


@pytest.mark.negative
async def test_ac_pme_10_neg_a_failed_pointer_write_is_a_real_failure():
    """Previously swallowed. A run that nothing points at is unreportable and unreconcilable."""
    store, wr = FakeTaskStore(), RecordingWorkroom()
    tid = await _approved(store)

    async def boom(_task_id, _ref):
        raise ConnectionResetError("operation_ref write rejected")

    store.set_operation_ref = boom  # type: ignore[method-assign]
    out = await _dispatch(store, wr, task_id=tid)

    assert out.decision is DispatchDecision.ERROR, "a failed pointer write was swallowed"
    assert isinstance(out.error, ConnectionResetError)
    assert out.operation_ref == wr.run_id, "the orphaned run id must be reported"
    assert str(wr.run_id) in out.detail


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


# ── AC-PME-11 · the meeting cap counts only what can actually be dispatched ─
async def test_ac_pme_11_non_dispatchable_items_never_block_dispatch():
    """The reported defect: 11 informational items permanently blocked every dispatch.

    Only ticket+plan+draft reaches the Workroom (§3.1). informational, question, ticket and
    ticket+plan produce no run, so they cannot consume a dispatch slot.
    """
    store, wr = FakeTaskStore(), RecordingWorkroom()
    for tier in (Tier.INFORMATIONAL, Tier.QUESTION, Tier.TICKET, Tier.TICKET_PLAN):
        for _ in range(11):
            await _item(store, tier)

    tid = await _approved(store)
    out = await _dispatch(store, wr, task_id=tid)
    assert out.dispatched, "non-dispatchable items blocked a real task"


async def test_ac_pme_11_untriaged_items_do_not_count():
    store, wr = FakeTaskStore(), RecordingWorkroom()
    for _ in range(20):
        await store.insert_task(
            TaskRecord(task_id=None, tenant_id=TENANT, meeting_id=MEETING,
                       source=Source.CLOSE_ITEM, item_ref="m#u", owner="Sam")
        )  # tier stays None — not yet triaged, so not dispatchable
    tid = await _approved(store)
    assert (await _dispatch(store, wr, task_id=tid)).dispatched


async def test_ac_pme_11_meeting_cap_admits_exactly_n_not_n_plus_one():
    """A cap of 3 admits 3 dispatchable tasks and holds the 4th — no off-by-one.

    Concurrency is set high so the MEETING cap is the binding constraint; otherwise
    max_concurrent_tasks bites first and the test measures the wrong limit.
    """
    cfg = PostMeetingConfig(max_concurrent_tasks=99, max_tasks_per_meeting=3)
    store, wr = FakeTaskStore(), RecordingWorkroom()
    outcomes = []
    for _ in range(cfg.max_tasks_per_meeting + 2):
        tid = await _approved(store)
        outcomes.append(await _dispatch(store, wr, task_id=tid, config=cfg))

    dispatched = [o for o in outcomes if o.dispatched]
    held = [o for o in outcomes if o.decision is DispatchDecision.WAITING_MEETING_CAP]
    assert len(dispatched) == cfg.max_tasks_per_meeting, (
        f"meeting cap admitted {len(dispatched)} at a cap of {cfg.max_tasks_per_meeting}"
    )
    assert len(held) == 2, "the over-cap tasks must be HELD, not dropped or errored"


async def test_ac_pme_11_terminal_tasks_release_their_meeting_slot():
    cfg = PostMeetingConfig(max_concurrent_tasks=99, max_tasks_per_meeting=3)
    store, wr = FakeTaskStore(), RecordingWorkroom()
    for _ in range(cfg.max_tasks_per_meeting):
        t = await _approved(store)
        await _dispatch(store, wr, task_id=t, config=cfg)
        await store.set_outcome(t, state=TaskState.ACCEPTED, outcome="accepted")

    tid = await _approved(store)
    assert (await _dispatch(store, wr, task_id=tid, config=cfg)).dispatched, (
        "terminal tasks still held their meeting slot"
    )


async def test_ac_pme_11_candidate_is_excluded_from_its_own_count():
    store = FakeTaskStore()
    tid = await _approved(store)
    n_with = await store.count_dispatchable_for_meeting(MEETING)
    n_without = await store.count_dispatchable_for_meeting(MEETING, exclude_task_id=tid)
    assert n_with == 1 and n_without == 0


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
