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
from uuid import UUID

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
