"""Workroom dispatch — bundle assembly + the pre-dispatch estimate gate + the
``workroom:<id>`` operation_runs persist (the DISPATCH side of §11.6).

Real work (trace deep impact, build a feature, run a simulation, write a report)
is bundled as a :class:`contracts.Bundle` — the ask verbatim + speaker +
timestamp + a ``notes_ref`` (= the meeting_id, §1.3) + the raw transcript tail +
a task_id (§11.5/D-026) — and dispatched to the Workroom, which does ALL the
thinking. The Workroom consumer is Doc 05 (not built here); this module builds
the dispatch against the Bundle contract + the operation_runs substrate.

Persistence follows §12.10: a Workroom task REUSES ``operation_runs``
(``operation_type='workroom:<id>'``, ``progress`` jsonb = the task bundle,
``result_ref`` = the terminal Envelope outbox) — there is NO ``workroom_tasks``
table. The bundle carries ``notes_ref``, NEVER the growing notes object (the
Workroom reads live notes via ``GET /internal/notes/{meeting_id}``): re-serializing
the notes object per ask is a real cost/latency trap over a multi-hour meeting.

The returned :class:`WorkroomHandle` is the in-flight handle a completion CALLBACK
later fills — the runtime delivers the done-moment and re-wakes Proxy (§3.2);
nothing polls. It is returnable as a :class:`contracts.Envelope` (the 05→04 result
shape) so the wake turn can present by channel / timing / still-relevance.

The pre-dispatch estimate gate (§12.7 / A-006) runs BEFORE any row is claimed:
an estimate over the remaining task budget asks approval and dispatches nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from contracts import Bundle, Envelope, EnvelopeStatus

from libs.db import Database
from libs.ops import DispatchDecision, MeetingCost
from libs.ops import dispatch_workroom as _estimate_gate

# A Workroom task is keyed as operation_type='workroom:<task-id>' (§12.10).
WORKROOM_OP_PREFIX = "workroom:"


def workroom_op_type(task_id: UUID | str) -> str:
    """The operation_runs.operation_type for a Workroom task (§12.10)."""
    return f"{WORKROOM_OP_PREFIX}{task_id}"


def assemble_bundle(
    *,
    ask: str,
    speaker: str,
    timestamp: datetime,
    meeting_id: UUID,
    transcript_tail: str = "",
    task_id: UUID,
) -> Bundle:
    """Assemble the ``contracts.Bundle`` handed 04→05 (§1.3/§11.5).

    ``notes_ref`` IS the ``meeting_id`` (a UUID handle) — the Workroom fetches the
    live notes object fresh through Doc 03's read path; the growing notes object
    is NEVER embedded here (§1.3). ``transcript_tail`` is a single string (D-026).
    """
    return Bundle(
        ask=ask,
        speaker=speaker,
        timestamp=timestamp,
        notes_ref=meeting_id,  # the handle, not the object (§1.3)
        transcript_tail=transcript_tail,
        task_id=task_id,
    )


@dataclass
class WorkroomHandle:
    """The in-flight handle for a dispatched Workroom task.

    Carries the ``task_id`` and the persisted ``run_id`` (the operation_runs row
    id — the returnable reference). The completion CALLBACK seam (:meth:`on_complete`
    / :meth:`set_result`) is how the runtime re-wakes Proxy on the terminal
    Envelope (§3.2) — a push, never a poll. :meth:`as_envelope` renders the
    handle as the 05→04 :class:`contracts.Envelope` result shape.
    """

    task_id: UUID
    run_id: Any
    bundle: Bundle
    _callback: Callable[[Envelope], Any] | None = field(default=None, repr=False)
    _result: Envelope | None = field(default=None, repr=False)

    def on_complete(self, callback: Callable[[Envelope], Any]) -> None:
        """Register the completion callback the runtime fires on the done-moment.

        If the result already landed (a race with an eager Workroom), fire now so
        the caller never misses the edge.
        """
        self._callback = callback
        if self._result is not None:
            callback(self._result)

    def set_result(self, envelope: Envelope) -> Envelope:
        """Deliver the terminal Envelope — fires the registered callback (push)."""
        self._result = envelope
        if self._callback is not None:
            self._callback(envelope)
        return envelope

    # Alias so a caller can `handle.complete(env)` symmetrically with set_result.
    complete = set_result

    def as_envelope(self, *, status: EnvelopeStatus = "partial") -> Envelope:
        """Render the handle as a ``contracts.Envelope``.

        Before completion this is an in-flight ``partial`` envelope carrying the
        ask headline + the task_id; after :meth:`set_result` it is the terminal
        Envelope the Workroom delivered.
        """
        if self._result is not None:
            return self._result
        return Envelope(
            headline=f"on it: {self.bundle.ask}",
            status=status,
            task_id=self.task_id,
        )


async def _claim_workroom_row(db: Database, bundle: Bundle) -> Any:
    """Atomically claim the ``workroom:<task_id>`` row with the Bundle in progress.

    The claim is an INSERT ... ON CONFLICT DO NOTHING against the partial unique
    index (§12.10 reuse-operation_runs), casting the meeting_id to text at this
    one call site (scope_id is the sole text column, §11.2). The Bundle is
    serialized into ``progress`` jsonb in the SAME insert — the durable task record.
    """
    op_type = workroom_op_type(bundle.task_id)
    progress = bundle.model_dump(mode="json")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO operation_runs
                (scope_id, operation_type, status, progress, created_by)
            VALUES ($1::text, $2, 'running', $3::jsonb, $4)
            ON CONFLICT (scope_id, operation_type) WHERE status = 'running'
            DO NOTHING
            RETURNING id
            """,
            str(bundle.notes_ref),  # notes_ref == meeting_id (§1.3)
            op_type,
            json.dumps(progress),
            db.instance_id,
        )
    if row is None:
        # A duplicate of an in-flight ask attaches rather than spawning (§3.15):
        # surface the already-owned run so the caller re-wakes off the same task.
        async with db.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM operation_runs "
                "WHERE scope_id = $1::text AND operation_type = $2 "
                "AND status = 'running' ORDER BY started_at DESC LIMIT 1",
                str(bundle.notes_ref),
                op_type,
            )
        return existing
    return row["id"]


async def dispatch_workroom(
    db: Database,
    bundle: Bundle,
    *,
    cost: MeetingCost | None = None,
    estimate_usd: float | None = None,
) -> WorkroomHandle | DispatchDecision:
    """Dispatch ``bundle`` to the Workroom via a persisted ``workroom:<id>`` row.

    When ``cost`` + ``estimate_usd`` are given the pre-dispatch estimate gate runs
    FIRST (§12.7 / A-006): an estimate over the remaining task budget returns the
    ``ask_approval`` :class:`~libs.ops.cost.DispatchDecision` and claims NO row.
    Otherwise (or once the gate passes) the ``workroom:<task_id>`` row is claimed
    with the Bundle in ``progress`` and a :class:`WorkroomHandle` is returned — the
    in-flight handle the completion callback fills (§3.2), returnable as an Envelope.

    Returns a :class:`DispatchDecision` when the estimate gate ran (so the caller
    sees the gate outcome), else a bare :class:`WorkroomHandle`.
    """
    gated = cost is not None and estimate_usd is not None
    if gated:
        # The estimate gate is polymorphic (returns Any); pin it to the sync
        # DispatchDecision it yields on the cost+estimate path.
        decision: DispatchDecision = _estimate_gate(cost=cost, estimate_usd=estimate_usd)
        if not decision.dispatched:
            return decision  # ask-approval: nothing claimed, nothing dispatched

    run_id = await _claim_workroom_row(db, bundle)
    handle = WorkroomHandle(task_id=bundle.task_id, run_id=run_id, bundle=bundle)

    if gated:
        # The gate passed AND the row is persisted — return the gate outcome so the
        # caller sees the dispatch decision (the live handle is recoverable from the
        # persisted workroom:<id> row by the run loop).
        return decision
    return handle


# ══════════════════════════════════════════════════════════════════════════════
# Doc 04 §112 — the registered tool wrapper + the completion callback.
#
# §112 assigns the harness "the registered tool functions (speak/chat/screen/dispatch/…
# — thin wrappers over the other docs' APIs)" and says "every dispatched workroom is an
# asyncio.create_task(...) with a done-callback — the runtime delivers the done-moment;
# nothing polls". Both were unbuilt; this is them. See
# docs/gaps/DOC04-WORKROOM-DISPATCH-UNWIRED.md for the gap this closes.
# ══════════════════════════════════════════════════════════════════════════════

#: The SDK MCP server name; the mounted tool path is mcp__dispatch_workroom__dispatch_workroom.
DISPATCH_SERVER_NAME = "dispatch_workroom"

#: The tool description. It carries real weight: SDK tool search is ON by default and
#: DEFERS SDK MCP tools, so Claude sees this string in a compact list and loads the schema
#: on demand. It therefore NAMES every parameter the handler reads — including the ones
#: absent from the dict schema. Python's dict schema treats every key as required, so an
#: optional parameter must be omitted from the schema, named in the description, and read
#: with args.get(). (drafts.py follows the args.get() half but never names `files`, so the
#: model has no way to learn that parameter exists — the failure this avoids.)
_DISPATCH_TOOL_DESCRIPTION = (
    "Dispatch real work to the Workroom. Use this when the ask needs code read, "
    "written, run or verified — not for a question you can answer directly. "
    "Required: 'ask' (what to do, verbatim from the room). "
    "Optional: 'speaker' (who asked; defaults to the room), "
    "'transcript_tail' (recent context, a single string). "
    "Returns immediately with a task_id; the result arrives later on its own. "
    "It does NOT wait for the work to finish, and it never pushes to a repository."
)

#: Strong refs to in-flight dispatch tasks. asyncio only holds a WEAK reference to a task,
#: so a task nobody keeps can be garbage-collected mid-run and simply vanish — the bug
#: provisioner.py:367 and server.py:214 already guard against the same way.
_INFLIGHT: set[Any] = set()


def _tool_ok(payload: dict[str, Any]) -> dict[str, Any]:
    """The SDK's success shape: JSON inside a text content block.

    The Python @tool decorator forwards only ``content`` and ``is_error`` — there is no
    ``structuredContent`` on this path — so a machine-readable result rides as JSON text.
    """
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _tool_err(reason: str, *, code: str) -> dict[str, Any]:
    """The SDK's error shape.

    The SDK already converts an uncaught exception into an error result, so catching is
    not about preventing a crash — it is about composing the message Claude READS. A raw
    exception string ("KeyError: 'ask'") tells the model nothing it can act on; a composed
    reason does. That is what AC-CON-003's never-throw contract actually buys.
    """
    return {
        "is_error": True,
        "content": [{"type": "text", "text": json.dumps({"code": code, "reason": reason})}],
    }


def run_and_notify(
    coro: Any,
    *,
    task_id: UUID,
    on_complete: Callable[[Any], None],
    label: str = "workroom",
) -> Any:
    """§112's ``create_task`` + done-callback. Returns the task; never awaits it.

    ``on_complete`` receives the terminal :class:`~contracts.Envelope`. It runs on the
    event loop inside the done-callback, so it MUST NOT await — it may only hand off
    synchronously (``queue.put_nowait`` for the live path, a scheduled write for
    post-meeting). ``run_loop._on_turn_complete`` is the same shape.

    ``SessionDriver.run_task`` never raises (Doc 05 Rule 6) and has already persisted its
    terminal Envelope into the run row before returning, so the normal path is simply
    reading ``task.result()``. A non-None ``task.exception()`` should therefore be
    unreachable; if it happens the task is NOT dropped — a synthesised ``failed`` Envelope
    is handed to ``on_complete`` and the fault is logged loudly, because a callback that
    swallowed it would lose the task silently.
    """
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    task = asyncio.ensure_future(coro)
    _INFLIGHT.add(task)

    def _done(finished: Any) -> None:
        _INFLIGHT.discard(finished)
        try:
            if finished.cancelled():
                envelope = _synth_failed(
                    f"{label} task was cancelled", task_id=task_id
                )
            else:
                exc = finished.exception()
                if exc is not None:
                    # Unreachable if Doc 05's Rule 6 holds. Loud, never silent.
                    log.exception(
                        "%s task raised, violating Rule 6 (run_task must never raise)",
                        label, exc_info=exc,
                    )
                    envelope = _synth_failed(
                        f"{label} task raised {type(exc).__name__}: {exc}",
                        task_id=task_id,
                    )
                else:
                    envelope = finished.result()
            on_complete(envelope)
        except BaseException:  # noqa: BLE001 - a failing callback must not lose the task
            log.exception("%s completion callback failed", label)

    task.add_done_callback(_done)
    return task


def _synth_failed(reason: str, *, task_id: UUID) -> Envelope:
    """A ``failed`` Envelope for a run that produced none. Never rounds up (Law 2).

    ``Envelope.task_id`` is required, so the caller must supply the task this stands in
    for — a synthesised envelope with no task id could not be reported against anything.
    ``receipts`` is empty and honest: there is nothing to cite when the run never returned.
    """
    return Envelope(
        headline="The work could not be completed.",
        detail=reason,
        receipts=[],
        status="failed",
        task_id=task_id,
    )


def make_dispatch_workroom_tool(
    *,
    db: Database,
    meeting_id: UUID,
    now: Callable[[], datetime],
    run_task: Callable[..., Any],
    on_complete: Callable[[Any], None],
    cost: MeetingCost | None = None,
    estimate_usd: float | None = None,
) -> Any:
    """The ``dispatch_workroom`` SDK tool, bound to ONE meeting (§112).

    Mirrors ``workroom.drafts.make_propose_change_tool``: a factory-per-query tool closing
    over the trusted host's ``db`` and this meeting's id, so the claim executes on the host.

    **``meeting_id`` comes from the bound context, never from ``args``.** The model supplies
    only the ask, the speaker and the transcript tail. There is deliberately no path by
    which a model-supplied ``meeting_id`` could be honoured — an emitted one is ignored
    rather than validated, because the bound value is the only one that exists here.

    **No ``annotations``.** ``readOnlyHint`` must stay at its default ``false``: the hint
    controls whether the SDK may batch a tool in PARALLEL with other read-only calls, and
    this tool claims an ``operation_runs`` row and starts real work. Two batched dispatches
    would race the partial unique index for no reason, and one would silently lose.

    Returns immediately with the claimed ``task_id``; it never awaits the run.
    """
    from claude_agent_sdk import tool

    @tool(
        DISPATCH_SERVER_NAME,
        _DISPATCH_TOOL_DESCRIPTION,
        {"ask": str},  # 'speaker'/'transcript_tail' are optional: named in the description
    )
    async def dispatch_workroom_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            ask = args.get("ask")
            if not isinstance(ask, str) or not ask.strip():
                return _tool_err(
                    "No ask was given. Pass 'ask' as the work to do, verbatim from the "
                    "room.",
                    code="missing_ask",
                )
            task_id = uuid4()
            bundle = assemble_bundle(
                ask=ask.strip(),
                speaker=str(args.get("speaker") or "the room"),
                timestamp=now(),
                meeting_id=meeting_id,  # BOUND — never args
                transcript_tail=str(args.get("transcript_tail") or ""),
                task_id=task_id,
            )
            outcome = await dispatch_workroom(
                db, bundle, cost=cost, estimate_usd=estimate_usd
            )
            run_id = getattr(outcome, "run_id", None)
            if run_id is None:
                # The cost gate declined: no row was claimed, so there is nothing running.
                return _tool_ok(
                    {
                        "accepted": False,
                        "reason": (
                            "The estimated cost exceeds this meeting's remaining task "
                            "budget, so nothing was started. Tell the room and ask "
                            "whether to spend it."
                        ),
                    }
                )
            run_and_notify(
                run_task(bundle, run_id=run_id),
                task_id=task_id,
                on_complete=on_complete,
            )
            return _tool_ok(
                {
                    "accepted": True,
                    "task_id": str(task_id),
                    "note": (
                        "Dispatched. It runs on its own and the result will reach you "
                        "when it finishes — do not wait for it, and do not dispatch it "
                        "again."
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - compose the message Claude reads
            return _tool_err(
                f"The work could not be dispatched ({type(exc).__name__}: {exc}). "
                "Nothing was started. Say so plainly rather than retrying blindly.",
                code="dispatch_failed",
            )

    return dispatch_workroom_tool


def make_dispatch_workroom_server(**kwargs: Any) -> Any:
    """The host-side in-process SDK MCP server carrying ``dispatch_workroom`` (§112).

    Minted factory-per-query (SDK MCP servers are connection-bound), exactly as
    ``workroom.drafts.make_propose_change_server`` is. Mounted into the wake turn's
    ``mcp_servers`` so the tool resolves as
    ``mcp__dispatch_workroom__dispatch_workroom`` — the name
    ``behaviors/propose_action.py`` already advertises in ``allowed_tools``.
    """
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(
        name=DISPATCH_SERVER_NAME,
        version="1.0.0",
        tools=[make_dispatch_workroom_tool(**kwargs)],
    )
