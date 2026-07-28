"""B6 — dispatch. Send an approved task into Doc 05's Workroom, unchanged.

Criteria: **AC-PME-09/-NEG** (Doc 04's bundle, Doc 05's Workroom, no second execution
path), **AC-PME-10/-NEG** (exactly one ``operation_runs`` row, surviving worker recycle),
**AC-PME-11/-NEG** (caps make dispatch wait) and **AC-PME-12/-NEG** (a cost estimate over
the ceiling asks the owner before the sandbox spins).

**There is no execution engine in this module.** It assembles nothing and runs nothing:
it calls ``harness.dispatch.assemble_bundle`` and ``harness.dispatch.dispatch_workroom``,
which already exist for the live path. Doc 07 §3.5 is emphatic that everything about how
the work happens is Doc 05's, and AC-PME-09 asserts statically that no second sandbox
provider, queue, scheduler or broker appears. The only thing added here is the decision of
*whether* to dispatch — caps, cost, and the approval gate.

**The run row is Doc 04's, not ours.** Per amendment P10 (ruling C-A) the row is keyed
``scope_id`` = meeting id and ``operation_type`` = ``workroom:{task_id}``, which is what
``harness.dispatch`` already does. ``post_meeting_tasks.operation_ref`` points at that row
and never mirrors its state — no status, no heartbeat, no progress column
(D07.1 / CANONICAL §12.11's ``workroom_tasks`` prohibition).

**Where it runs:** a ``meeting_runtime`` with ``media_session=False``
(:func:`post_meeting_worker`). Not a new deployable — a mode on the runtime that already
exists. This is the resolution of the contradiction the sealed bundle recorded in
``assurance_limits``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .approval import may_dispatch
from .config import PostMeetingConfig, load_post_meeting_config
from .models import TaskState

log = logging.getLogger(__name__)


class DispatchDecision(str, Enum):
    """Why dispatch did or did not happen. Every refusal is a named, reportable reason."""

    DISPATCHED = "dispatched"
    NOT_APPROVED = "not-approved"
    WAITING_CONCURRENCY = "waiting-concurrency-cap"
    WAITING_MEETING_CAP = "waiting-meeting-cap"
    COST_ASK = "cost-ask"
    ERROR = "error"


@dataclass
class DispatchOutcome:
    task_id: Any
    decision: DispatchDecision
    operation_ref: Any = None
    envelope: Any = None
    estimated_cost_usd: Optional[float] = None
    error: Optional[BaseException] = None
    detail: str = ""

    @property
    def dispatched(self) -> bool:
        return self.decision is DispatchDecision.DISPATCHED

    @property
    def waiting(self) -> bool:
        """A held task is NOT a dropped task — it becomes dispatchable when a slot frees."""
        return self.decision in {
            DispatchDecision.WAITING_CONCURRENCY,
            DispatchDecision.WAITING_MEETING_CAP,
            DispatchDecision.COST_ASK,
        }


def post_meeting_worker(
    *, header: Any, carrier: Any, db: Any, host_budget: Any, **kwargs: Any
) -> Any:
    """Build the ``meeting_runtime`` worker Doc 07 §3.5 specifies — with no media session.

    No transport, no Scribe, no tile, no consent gate and no STT loop are constructed. The
    runtime refuses :meth:`start` and :meth:`ingest_transcript` in this mode, so it cannot
    be turned into an observing one. **No new deployable** — this is the existing
    ``MeetingRuntime`` in its no-media mode.
    """
    from ..meeting_runtime import MeetingRuntime

    return MeetingRuntime(
        header=header,
        carrier=carrier,
        db=db,
        host_budget=host_budget,
        media_session=False,
        **kwargs,
    )


async def check_caps(
    *,
    tenant_id: Any,
    meeting_id: Any,
    store: Any,
    config: PostMeetingConfig,
) -> Optional[DispatchDecision]:
    """Return the cap that is blocking dispatch, or ``None`` if there is room.

    Reads the LIVE counts rather than an in-process counter, so two workers cannot each
    believe they hold the last slot. A count that cannot be read blocks dispatch
    (AC-PME-11-NEG: an unreadable count is never treated as zero).
    """
    running = await store.count_running_for_tenant(tenant_id)
    if running >= config.max_concurrent_tasks:
        return DispatchDecision.WAITING_CONCURRENCY
    per_meeting = await store.count_for_meeting(meeting_id)
    if per_meeting > config.max_tasks_per_meeting:
        return DispatchDecision.WAITING_MEETING_CAP
    return None


async def run_dispatch(
    *,
    task_id: Any,
    tenant_id: Any,
    meeting_id: Any,
    ask: str,
    speaker: str,
    timestamp: Any,
    transcript_tail: str = "",
    store: Any,
    workroom_dispatch: Any,
    assemble_bundle: Any,
    estimate_cost: Any = None,
    cost_answered: bool = False,
    config: Optional[PostMeetingConfig] = None,
) -> DispatchOutcome:
    """Dispatch one approved task. Never raises.

    Order is the safety property. Approval, then caps, then cost — each a hard stop before
    the next. The sandbox is only ever reached through ``workroom_dispatch``, which is the
    last thing called and only on the fully-cleared path.
    """
    cfg = config if config is not None else load_post_meeting_config()

    # 1. Approval. RUNNING is entered only from APPROVED (§3.9, D07.2).
    try:
        row = await store.get(task_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable gate is a CLOSED gate
        log.exception("dispatch: approval lookup failed for %s", task_id)
        return DispatchOutcome(task_id, DispatchDecision.ERROR, error=exc,
                               detail="approval lookup failed")
    if not may_dispatch(row):
        return DispatchOutcome(task_id, DispatchDecision.NOT_APPROVED,
                               detail="no named-human approval on the task record")

    # 2. Caps. A blocked task WAITS; it is never dropped (AC-PME-11).
    try:
        blocked = await check_caps(
            tenant_id=tenant_id, meeting_id=meeting_id, store=store, config=cfg
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable count is NOT zero
        log.exception("dispatch: cap read failed for %s; holding", task_id)
        return DispatchOutcome(task_id, DispatchDecision.WAITING_CONCURRENCY, error=exc,
                               detail="cap count unreadable; holding rather than admitting")
    if blocked is not None:
        return DispatchOutcome(task_id, blocked, detail="cap reached; waiting for a slot")

    # 3. Cost, BEFORE the sandbox spins (§3.5). An unavailable estimate is not "cheap".
    estimate: Optional[float] = None
    if estimate_cost is not None and not cost_answered:
        try:
            estimate = float(await estimate_cost(task_id))
        except Exception as exc:  # noqa: BLE001 - missing estimate => ask, never spend
            log.exception("dispatch: cost estimate failed for %s; asking owner", task_id)
            return DispatchOutcome(task_id, DispatchDecision.COST_ASK, error=exc,
                                   detail="cost estimate unavailable; asking the owner")
        if estimate > cfg.task_cost_ceiling:
            return DispatchOutcome(
                task_id, DispatchDecision.COST_ASK, estimated_cost_usd=estimate,
                detail=f"estimate {estimate} exceeds ceiling {cfg.task_cost_ceiling}",
            )

    # 4. Cleared. Doc 04's bundle → Doc 05's Workroom, unchanged.
    try:
        bundle = assemble_bundle(
            ask=ask,
            speaker=speaker,
            timestamp=timestamp,
            meeting_id=meeting_id,
            transcript_tail=transcript_tail,
            task_id=task_id,
        )
        await store.set_state(task_id, TaskState.RUNNING)
        envelope = await workroom_dispatch(bundle)
    except Exception as exc:  # noqa: BLE001 - report plainly; NEVER fall back to another path
        log.exception("dispatch: workroom dispatch failed for %s", task_id)
        return DispatchOutcome(task_id, DispatchDecision.ERROR, error=exc,
                               estimated_cost_usd=estimate,
                               detail="workroom dispatch failed; no fallback path exists")

    operation_ref = getattr(bundle, "task_id", None)
    try:
        await store.set_operation_ref(task_id, operation_ref)
    except Exception:  # noqa: BLE001 - the run is real even if the pointer write failed
        log.exception("dispatch: could not record operation_ref for %s", task_id)

    return DispatchOutcome(
        task_id, DispatchDecision.DISPATCHED, operation_ref=operation_ref,
        envelope=envelope, estimated_cost_usd=estimate,
    )
