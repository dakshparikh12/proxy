"""Durable access for ``post_meeting_tasks`` and ``clarify_items``.

Parameterised SQL matched to migrations 0009/0010, in the same thin-repo style as
``libs/db/src/db/repos/*``: the raw SQL is the single source of truth and there is no ORM.

Doc 07 §3.8 permits this component to write its own task record and ``clarify_items``
(co-owned with Doc 06), and nothing else. There is deliberately no function here that
writes ``staged_drafts``, the notes object, ``meeting_cost``, or ``operation_runs`` —
those belong to Doc 05, Doc 03 and Doc 04 respectively, and B6 reaches ``operation_runs``
only through Doc 04's existing dispatch path.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import (
    DISPATCHABLE_TIERS,
    TERMINAL_STATES,
    Source,
    TaskRecord,
    TaskState,
    Tier,
)

# Column list is spelled out at each call site rather than interpolated from a constant:
# an f-string carrying a SQL fragment is indistinguishable from an injection site to a
# static analyser (bandit B608), and the literal form is no harder to read.


class PostMeetingTaskStore:
    """``post_meeting_tasks`` — lifecycle state only, never a second run table.

    Note what is absent: no ``status``, no ``progress``, no ``last_heartbeat_at``. The run
    is the ``operation_runs`` row (D07.1), and ``operation_ref`` points at it rather than
    mirroring it. Adding a heartbeat column here would recreate the ``workroom_tasks``
    table CANONICAL §12.11 rejects.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert_task(self, task: TaskRecord) -> Any:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO post_meeting_tasks
                    (tenant_id, meeting_id, source, item_ref, tier, owner, state)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING task_id
                """,
                task.tenant_id,
                task.meeting_id,
                task.source.value if isinstance(task.source, Source) else task.source,
                task.item_ref,
                task.tier.value if isinstance(task.tier, Tier) else task.tier,
                task.owner,
                task.state.value if isinstance(task.state, TaskState) else task.state,
            )
            return row["task_id"]

    async def set_tier(self, task_id: Any, tier: Tier, *, state: TaskState) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks SET tier = $2, state = $3, updated_at = now() "
                "WHERE task_id = $1",
                task_id,
                tier.value,
                state.value,
            )

    async def set_state(self, task_id: Any, state: TaskState) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks SET state = $2, updated_at = now() "
                "WHERE task_id = $1",
                task_id,
                state.value,
            )

    async def set_plan(self, task_id: Any, plan: str, *, state: TaskState) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks SET plan = $2, state = $3, updated_at = now() "
                "WHERE task_id = $1",
                task_id,
                plan,
                state.value,
            )

    async def approve(self, task_id: Any, *, approved_by: str, approved_at: Any) -> None:
        """Write APPROVED with its approver in ONE statement.

        Deliberately not two updates. The CHECK constraint
        ``state <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)``
        would reject a first statement that set the state without the approver, so a
        two-step approval cannot exist even transiently (D07.2 / AC-PME-07).
        """
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks "
                "   SET state = 'APPROVED', approved_by = $2, approved_at = $3, "
                "       updated_at = now() "
                " WHERE task_id = $1",
                task_id,
                approved_by,
                approved_at,
            )

    async def set_operation_ref(self, task_id: Any, operation_ref: Any) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks SET operation_ref = $2, updated_at = now() "
                "WHERE task_id = $1",
                task_id,
                operation_ref,
            )

    async def set_outcome(
        self,
        task_id: Any,
        *,
        state: TaskState,
        outcome: str,
        draft_id: Any = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE post_meeting_tasks "
                "   SET state = $2, outcome = $3, "
                "       draft_id = COALESCE($4, draft_id), "
                "       cost_usd = COALESCE($5, cost_usd), updated_at = now() "
                " WHERE task_id = $1",
                task_id,
                state.value,
                outcome,
                draft_id,
                cost_usd,
            )

    async def get(self, task_id: Any) -> Optional[dict[str, Any]]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT task_id, tenant_id, meeting_id, source, item_ref, tier, owner, "
                "       state, plan, approved_by, approved_at, operation_ref, draft_id, "
                "       cost_usd, outcome "
                "  FROM post_meeting_tasks WHERE task_id = $1",
                task_id,
            )
            return dict(row) if row is not None else None

    async def count_running_for_tenant(self, tenant_id: Any) -> int:
        """Backs ``max_concurrent_tasks`` (Doc 07 §3.5 / AC-PME-11).

        Reads the live count rather than an in-process counter, so two workers cannot each
        believe they hold the last slot.
        """
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM post_meeting_tasks "
                " WHERE tenant_id = $1 AND state = 'RUNNING'",
                tenant_id,
            )
            return int(row["n"])

    async def count_dispatchable_for_meeting(
        self, meeting_id: Any, *, exclude_task_id: Any = None
    ) -> int:
        """Backs ``max_tasks_per_meeting`` (Doc 07 §3.5 / AC-PME-11).

        Counts only tasks that can actually occupy a dispatch slot:

        * **dispatchable tier** — ``ticket+plan+draft`` is the only tier that reaches the
          Workroom (§3.1). Counting every row meant a meeting with eleven *informational*
          items — items that by definition produce nothing — permanently blocked all
          dispatch for that meeting. An untiered row (tier IS NULL) has not been triaged
          yet and cannot be dispatched, so it does not count either.
        * **non-terminal state** — a task that has been accepted, had changes requested,
          or been discarded is finished and is not holding a slot.

        ``exclude_task_id`` leaves the candidate out of its own count, so the caller's
        comparison reads "are there already N others?" and can use ``>=`` exactly like the
        concurrency check. Mixing an inclusive count with ``>`` and an exclusive count with
        ``>=`` is what produced the off-by-one.
        """
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM post_meeting_tasks "
                " WHERE meeting_id = $1 "
                "   AND tier = ANY($2::text[]) "
                "   AND state <> ALL($3::text[]) "
                "   AND ($4::uuid IS NULL OR task_id <> $4::uuid)",
                meeting_id,
                [t.value for t in DISPATCHABLE_TIERS],
                [s.value for s in TERMINAL_STATES],
                exclude_task_id,
            )
            return int(row["n"])


class ClarifyItemStore:
    """``clarify_items`` — co-owned with Doc 06 (ruling C-C), created by migration 0010.

    This is the ONE table Doc 07 may write before a plan is approved (ruling C-D). The
    carve-out is closed: asking a question is not a world-change.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert(
        self,
        *,
        tenant_id: Any,
        meeting_id: Any,
        question: str,
        kind: Optional[str] = None,
        blocking_ref: Optional[str] = None,
        urgency: Optional[str] = None,
    ) -> Any:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO clarify_items
                    (tenant_id, meeting_id, question, kind, blocking_ref, urgency)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING clarify_id
                """,
                tenant_id,
                meeting_id,
                question,
                kind,
                blocking_ref,
                urgency,
            )
            return row["clarify_id"]

    async def pending_for_meeting(self, meeting_id: Any) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT clarify_id, question, kind, blocking_ref, urgency "
                "  FROM clarify_items "
                " WHERE meeting_id = $1 AND answer IS NULL "
                " ORDER BY created_at",
                meeting_id,
            )
            return [dict(r) for r in rows]
