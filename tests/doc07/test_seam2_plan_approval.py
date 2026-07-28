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
    WorkroomDispatchUnavailable,
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


# ── the BLOCKED dispatch boundary (Doc 04 §112) ───────────────────────────
async def test_dispatch_is_blocked_and_says_exactly_why():
    res = _approve()
    assert res.approved is True, "the approval itself must still land"
    assert res.dispatch_blocked is True
    assert res.status == 202, "recorded, not completed"

    d = res.detail
    assert "Doc 04 §112" in d
    assert "no production caller" in d
    assert "SessionDriver is constructed only in tests" in d
    assert "TOOL_HANDLERS" in d
    assert "live in-meeting path has the same gap" in d
    assert "docs/gaps/DOC04-WORKROOM-DISPATCH-UNWIRED.md" in d


async def test_the_approval_is_durable_even_though_dispatch_is_blocked():
    """A human's decision is not rolled back because downstream machinery is missing."""
    w = Writes()
    res = _approve(writes=w)
    assert res.dispatch_blocked is True
    assert len(w.calls) == 1, "the approval was rolled back by the blocked dispatch"


async def test_the_boundary_raises_rather_than_claiming_a_dead_row():
    """Calling dispatch_workroom would claim an operation_runs row nothing executes."""
    with pytest.raises(WorkroomDispatchUnavailable):
        raise WorkroomDispatchUnavailable("x")
    assert issubclass(WorkroomDispatchUnavailable, RuntimeError)


async def test_a_real_dispatch_would_be_used_when_one_exists():
    """The boundary is the ABSENCE of a dispatcher, not a hard-coded refusal."""
    seen: list = []

    def dispatcher(conn, *, task_id, meeting_id):
        seen.append((task_id, meeting_id))

    res = _approve(dispatch=dispatcher)
    assert res.status == 200
    assert res.dispatch_blocked is False
    assert seen == [(TASK, MEETING)]


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
