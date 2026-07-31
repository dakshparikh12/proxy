"""SEAM 2 — a named human approves a PLANNED task. ``POST /m/{meeting_id}/tasks/{task_id}/approve``

Doc 07 §3.4's **plan approval**, which is a different thing from
``accept_route.py``'s **draft acceptance**:

* plan approval happens **before** any work runs — it is the gate that lets a task start;
* draft acceptance happens **after** — it is the human click that lands the artifact.

Both are named-human actions on durable storage in ``control_plane``, and both must work
long after the meeting harness is gone (R1's confirm-at-build: the handler executes in
``control_plane``, never inside a torn-down harness process).

**The human click is the trigger.** There is no poller, no scheduler and no queue here,
and none may be added: Doc 07 §3.4 says *"Proxy does not nag and never proceeds by
default"*, and a background sweep that dispatched approved tasks would be proceeding by
default. The only sweep in Doc 07 is B4's expiry, which closes tasks rather than starting
them.

**Dispatch is wired.** It used to be a declared BLOCKED boundary: ``dispatch=None`` raised
``WorkroomDispatchUnavailable``, because Doc 04 §112's tool wrapper and completion callback
did not exist and calling into them would have claimed an ``operation_runs`` row that nothing
executed. §112 is built, so the refusal and the exception it raised are gone, and
``harness.post_meeting.wire.make_plan_dispatcher`` is what goes in ``dispatch=``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, mirrors accept_route
    from fastapi import FastAPI

_LOG = logging.getLogger("control_plane.plan_approval")

APPROVE_PATH = "/m/{meeting_id}/tasks/{task_id}/approve"


@dataclass(frozen=True)
class ApproveResponse:
    """The route's typed response. Mirrors ``accept_route.AcceptResponse``'s shape."""

    status: int
    approved: bool = False
    #: True when the approval landed but the task did not start. Approved-and-unstarted is a
    #: real state and must be distinguishable from approved-and-running: the task is still
    #: APPROVED, so it is re-dispatchable, and nothing about the human's click was lost.
    dispatch_blocked: bool = False
    task_id: Optional[str] = None
    approved_by: Optional[str] = None
    detail: str = ""


def _authenticated(request: Any) -> bool:
    return bool(getattr(request, "user", None)) and bool(getattr(request, "tenant", None))


def handle_approve_plan(
    conn: Any,
    *,
    request: Any,
    meeting_id: str,
    task_id: str,
    now: Any,
    load_task: Callable[[Any, str], Optional[dict[str, Any]]],
    write_approved: Callable[..., None],
    dispatch: Optional[Callable[..., Any]] = None,
    audit_sink: Optional[Callable[[Any], None]] = None,
) -> ApproveResponse:
    """Approve a PLANNED task, then attempt dispatch.

    Fail-closed order, matching ``handle_accept``: auth → server-side tenant → task
    exists → state is PLANNED → write APPROVED → audit → dispatch.

    ``write_approved`` sets ``state``, ``approved_by`` and ``approved_at`` in ONE
    statement. Migration 0009's CHECK rejects an APPROVED row missing either approver
    field, so a two-step approval cannot exist even transiently.
    """
    # (1) Authentication — an unauthenticated caller cannot approve. Approval is the
    #     named-human gate (D07.2); an anonymous approval is a contradiction in terms.
    if not _authenticated(request):
        return ApproveResponse(status=401, detail="unauthenticated")

    approver = str(getattr(request, "user", "") or "").strip()

    # (2) Server-side tenant check — never trust a tenant supplied by the caller.
    row = load_task(conn, task_id)
    if row is None:
        return ApproveResponse(status=404, detail="no such task")
    if str(row.get("meeting_id")) != str(meeting_id):
        return ApproveResponse(status=404, detail="task does not belong to this meeting")
    caller_tenant = getattr(request, "tenant", None)
    if str(row.get("tenant_id")) != str(caller_tenant):
        # 404, not 403: never confirm a task exists to another tenant (doc08 anti-leak).
        return ApproveResponse(status=404, detail="no such task")

    # (3) Only a PLANNED task may be approved. RUNNING is entered only from APPROVED
    #     (§3.9), and approving something already running or discarded is meaningless.
    if row.get("state") != "PLANNED":
        return ApproveResponse(
            status=409, detail=f"task is {row.get('state')!r}, not PLANNED"
        )

    # (4) The approval itself — one statement, both approver fields (D07.2).
    from .post_meeting.approval import is_named_human

    if not is_named_human(approver):
        return ApproveResponse(status=403, detail="approver is not a named human")
    write_approved(conn, task_id=task_id, approved_by=approver, approved_at=now)

    if audit_sink is not None:
        audit_sink(
            f"approve-plan meeting={meeting_id} task={task_id} "
            f"tenant={caller_tenant} user={approver}"
        )

    # (5) Dispatch. The approval above has ALREADY landed and is NOT rolled back by a
    #     dispatch failure — a human's decision is durable whatever happens downstream of
    #     it. The task stays APPROVED, which is a re-dispatchable state, rather than being
    #     dragged back to PLANNED and asking the same human the same question again.
    if dispatch is None:
        # No dispatcher configured. Previously this raised WorkroomDispatchUnavailable
        # because §112's wrapper did not exist; it does now, so a missing dispatcher is a
        # wiring fault in whoever mounted the route, not a property of the system. Reported
        # as 202 (approved, not started) so the human's click is never silently discarded.
        _LOG.error(
            "plan approved but no dispatcher is configured on this route; task %s stays "
            "APPROVED and unstarted. Pass dispatch=make_plan_dispatcher(db=...) at install.",
            task_id,
        )
        return ApproveResponse(
            status=202, approved=True, dispatch_blocked=True, task_id=task_id,
            approved_by=approver,
            detail="approved; no dispatcher is configured, so the task has not started",
        )
    try:
        dispatch(conn, task_id=task_id, meeting_id=meeting_id)
    except Exception as exc:  # noqa: BLE001 - the approval stands; report, never unwind
        # A dispatch that fails to START is reported as 202, not 500: the human's decision
        # is recorded and durable, and the task is retryable from APPROVED. A 500 would
        # invite the caller to retry the APPROVAL, which would then 409 on the state check
        # and read as "your click did not work" when in fact it did.
        _LOG.exception("plan approved but dispatch could not start for task %s", task_id)
        return ApproveResponse(
            status=202, approved=True, dispatch_blocked=True, task_id=task_id,
            approved_by=approver,
            detail=(
                f"approved; the task did not start ({type(exc).__name__}) and remains "
                "APPROVED, so it can be dispatched again"
            ),
        )

    return ApproveResponse(
        status=200, approved=True, task_id=task_id, approved_by=approver,
        detail="approved and dispatched",
    )


def install_approve_route(
    app: "FastAPI",
    *,
    dependencies: "list[Any] | None" = None,
    audit_sink: "Callable[[Any], None] | None" = None,
    load_task: "Callable[[Any, str], Optional[dict[str, Any]]] | None" = None,
    write_approved: "Callable[..., None] | None" = None,
    dispatch: "Callable[..., Any] | None" = None,
) -> None:
    """Mount ``POST /m/{meeting_id}/tasks/{task_id}/approve`` BEHIND the auth wall.

    Mirrors ``install_accept_route``: ``dependencies`` carries the §4.6 ``protected()``
    wrapper so a fail-closed 401/403 fires server-side BEFORE the handler, and so
    ``tests/security/test_routes_are_scoped.py`` classifies this mutation as tenant-scoped
    rather than raw. The handler keeps its own auth/tenant/state checks as defence in
    depth — plan approval is the gate that lets work start (D07.2).

    Pass ``dispatch=make_plan_dispatcher(db=...)`` (``harness.post_meeting.wire``). It is
    still keyword-optional so a caller can mount the approval gate alone, but a route mounted
    without it approves tasks that never start, and says so at ERROR and in the 202 body.
    """
    from fastapi import Request, Response
    from fastapi.responses import JSONResponse

    sink = audit_sink if audit_sink is not None else _default_audit_sink
    loader = load_task if load_task is not None else _load_task_row
    writer = write_approved if write_approved is not None else _write_approved

    @app.post(APPROVE_PATH, include_in_schema=True, dependencies=dependencies or [])
    async def approve_plan_route(
        meeting_id: str, task_id: str, request: Request
    ) -> Response:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return Response(status_code=503)  # no durable substrate handle -> honest 503

        from datetime import datetime, timezone

        async with db.acquire() as aconn:  # noqa: F841 - async pool handle
            resp = handle_approve_plan(
                aconn,
                request=request,
                meeting_id=meeting_id,
                task_id=task_id,
                now=datetime.now(timezone.utc),
                load_task=loader,
                write_approved=writer,
                dispatch=dispatch,
                audit_sink=sink,
            )
        if resp.status not in (200, 202):
            return Response(status_code=resp.status)
        return JSONResponse(
            {
                "approved": resp.approved,
                "dispatch_blocked": resp.dispatch_blocked,
                "task_id": resp.task_id,
                "approved_by": resp.approved_by,
                "detail": resp.detail,
            },
            status_code=resp.status,
        )


def _default_audit_sink(record: Any) -> None:
    """Record a plan approval on the durable audit channel (never a secret)."""
    _LOG.info("%s", record)


def _load_task_row(conn: Any, task_id: str) -> Optional[dict[str, Any]]:
    """Read the task's tenant/meeting/state for the server-side checks."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT task_id, tenant_id, meeting_id, state FROM post_meeting_tasks "
            " WHERE task_id = %s",
            (task_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "task_id": row[0], "tenant_id": row[1], "meeting_id": row[2], "state": row[3]
    }


def _write_approved(
    conn: Any, *, task_id: str, approved_by: str, approved_at: Any
) -> None:
    """State + both approver fields in ONE statement (D07.2).

    Migration 0009's CHECK rejects an APPROVED row missing either field, so a two-step
    approval cannot exist even transiently.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE post_meeting_tasks "
            "   SET state = 'APPROVED', approved_by = %s, approved_at = %s, "
            "       updated_at = now() "
            " WHERE task_id = %s AND state = 'PLANNED'",
            (approved_by, approved_at, task_id),
        )
