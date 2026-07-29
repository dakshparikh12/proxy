"""SEAM 2 — plan approval in control_plane. Criteria: AC-PME-07, AC-PME-07-NEG.

Doc 07 §3.4 plan approval, distinct from accept_route.py's draft acceptance. The dispatch
half is a declared BLOCKED boundary (Doc 04 §112) and these tests assert it is honestly
blocked rather than quietly passing over a dead end.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from control_plane.plan_approval_route import (
    APPROVE_PATH,
    ApproveResponse,
    handle_approve_plan,
)

pytestmark = pytest.mark.asyncio

TENANT, MEETING, TASK = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class Req:
    def __init__(self, user="Priya", tenant=TENANT):
        self.user, self.tenant = user, tenant


def row(**over):
    base = {
        "task_id": TASK, "meeting_id": MEETING, "tenant_id": TENANT,
        "state": "PLANNED", "owner": "Sam",
    }
    base.update(over)
    return base


class Writes:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, conn, *, task_id, approved_by, approved_at):
        self.calls.append(
            {"task_id": task_id, "approved_by": approved_by, "approved_at": approved_at}
        )


def _approve(*, request=None, task=None, dispatch=None, writes=None, audit=None):
    return handle_approve_plan(
        object(),
        request=request or Req(),
        meeting_id=MEETING,
        task_id=TASK,
        now=NOW,
        load_task=lambda _c, _t: task if task is not None else row(),
        write_approved=writes or Writes(),
        dispatch=dispatch,
        audit_sink=audit,
    )


# ── AC-PME-07 · APPROVED is written only by a named human ─────────────────
async def test_ac_pme_07_approval_writes_both_approver_fields_in_one_call():
    w = Writes()
    res = _approve(writes=w)
    assert res.approved is True
    assert len(w.calls) == 1
    call = w.calls[0]
    assert call["approved_by"] == "Priya"
    assert call["approved_at"] == NOW
    assert call["task_id"] == TASK


async def test_ac_pme_07_the_route_lives_in_control_plane_not_the_harness():
    """R1's confirm-at-build: approval must survive a torn-down harness process."""
    import control_plane.plan_approval_route as m

    assert m.__name__.startswith("control_plane."), m.__name__
    assert APPROVE_PATH == "/m/{meeting_id}/tasks/{task_id}/approve"


async def test_ac_pme_07_plan_approval_is_a_distinct_path_from_draft_accept():
    from control_plane.accept_route import ACCEPT_PATH

    assert APPROVE_PATH != ACCEPT_PATH
    assert "/tasks/" in APPROVE_PATH and "/drafts/" in ACCEPT_PATH


async def test_ac_pme_07_audit_records_the_approver():
    seen: list[str] = []
    _approve(audit=seen.append)
    assert len(seen) == 1
    assert "user=Priya" in seen[0] and "approve-plan" in seen[0]


# ── AC-PME-07-NEG · the gate refuses everything ambiguous ─────────────────
@pytest.mark.negative
async def test_ac_pme_07_neg_unauthenticated_cannot_approve():
    w = Writes()
    for bad in (Req(user=None), Req(tenant=None), Req(user="", tenant="")):
        res = _approve(request=bad, writes=w)
        assert res.status == 401
        assert res.approved is False
    assert w.calls == [], "an unauthenticated caller wrote APPROVED"


@pytest.mark.negative
async def test_ac_pme_07_neg_another_tenant_gets_404_not_403():
    """Never confirm a task exists to another tenant (doc08 anti-leak)."""
    w = Writes()
    res = _approve(request=Req(tenant=str(uuid.uuid4())), writes=w)
    assert res.status == 404
    assert w.calls == []


@pytest.mark.negative
async def test_ac_pme_07_neg_task_from_another_meeting_is_404():
    w = Writes()
    res = _approve(task=row(meeting_id=str(uuid.uuid4())), writes=w)
    assert res.status == 404
    assert w.calls == []


@pytest.mark.negative
async def test_ac_pme_07_neg_missing_task_is_404():
    w = Writes()
    res = handle_approve_plan(
        object(), request=Req(), meeting_id=MEETING, task_id=TASK, now=NOW,
        load_task=lambda _c, _t: None, write_approved=w,
    )
    assert res.status == 404 and w.calls == []


@pytest.mark.negative
@pytest.mark.parametrize(
    "state", ["EXTRACTED", "TRIAGED", "CLARIFYING", "APPROVED", "RUNNING", "DISCARDED"]
)
async def test_ac_pme_07_neg_only_a_planned_task_may_be_approved(state):
    w = Writes()
    res = _approve(task=row(state=state), writes=w)
    assert res.status == 409
    assert w.calls == [], f"a {state} task was approved"


@pytest.mark.negative
async def test_ac_pme_07_neg_a_non_named_human_cannot_approve():
    w = Writes()
    for bad in ("UNRESOLVED", "SYSTEM", "Proxy", "   "):
        res = _approve(request=Req(user=bad), writes=w)
        assert res.status in (401, 403), bad
        assert res.approved is False
    assert w.calls == []


# ── dispatch, now that §112 is built ──────────────────────────────────────
async def test_the_dispatcher_is_called_with_the_task_and_meeting():
    """The route's whole remaining job after the write: hand off to the dispatcher."""
    seen: list = []

    def dispatcher(conn, *, task_id, meeting_id):
        seen.append((task_id, meeting_id))

    res = _approve(dispatch=dispatcher)
    assert res.status == 200
    assert res.dispatch_blocked is False
    assert res.detail == "approved and dispatched"
    assert seen == [(TASK, MEETING)]


async def test_a_route_mounted_without_a_dispatcher_says_so_rather_than_raising():
    """Was WorkroomDispatchUnavailable. §112 exists, so a missing dispatcher is a WIRING
    fault in whoever mounted the route — reported, not modelled as a system property."""
    w = Writes()
    res = _approve(writes=w)
    assert res.approved is True, "the approval itself must still land"
    assert res.dispatch_blocked is True
    assert res.status == 202, "recorded, not completed"
    assert "no dispatcher is configured" in res.detail
    assert len(w.calls) == 1, "the approval was rolled back by the missing dispatcher"


async def test_a_dispatcher_that_raises_never_unwinds_the_approval():
    """The human's decision is durable whatever happens downstream of it.

    202 rather than 500 on purpose: a 500 invites the caller to retry the APPROVAL, which
    would then 409 on the PLANNED check and read as "your click did not work" when it did.
    """
    w = Writes()

    def exploding(conn, *, task_id, meeting_id):
        raise RuntimeError("sandbox pool exhausted")

    res = _approve(dispatch=exploding, writes=w)
    assert res.status == 202
    assert res.approved is True
    assert res.dispatch_blocked is True
    assert len(w.calls) == 1, "a failed dispatch rolled back the approval"
    assert "RuntimeError" in res.detail, "the failure must be nameable by a human"
    assert "remains APPROVED" in res.detail, "the retryable state must be stated"


async def test_the_production_dispatcher_matches_the_route_signature():
    """The route and ``make_plan_dispatcher`` must actually fit — a wiring test, not a mock.

    ``_approve`` cannot call the real one (it schedules onto the running loop and reaches
    Postgres), so this asserts the contract between them: it accepts the route's exact call
    shape, and it returns None rather than an awaitable the sync route would silently drop.
    """
    import inspect

    from harness.post_meeting.wire import make_plan_dispatcher

    dispatcher = make_plan_dispatcher(db=object(), store=object())
    assert not inspect.iscoroutinefunction(dispatcher), (
        "a coroutine function here would be scheduled by nobody — the route is sync"
    )
    sig = inspect.signature(dispatcher)
    sig.bind(object(), task_id=TASK, meeting_id=MEETING)  # raises if the shapes diverge


async def test_no_poller_scheduler_or_queue_exists_in_this_route():
    """§3.4: the human click is the trigger; a sweep would be proceeding by default."""
    import ast
    import pathlib

    src = pathlib.Path(
        "services/harness/src/control_plane/plan_approval_route.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for mod in imported:
        low = (mod or "").lower()
        for banned in ("sched", "celery", "apscheduler", "queue", "cron", "asyncio"):
            assert banned not in low, f"the approval route imported {mod}"
    assert isinstance(ApproveResponse(status=200), ApproveResponse)
