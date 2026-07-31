"""SEAM 1 — close completion triggers intake.

Doc 07 §2: *"This doc begins after the record is written, and only reads it."*
:func:`run_intake` is what the close path calls once ``run_close_pass`` has returned, and
it runs B1 → B2 → B3 → B4: extract, triage, clarify, plan.

**The isolation promise is structural.** :func:`run_intake` is a total function — it
catches everything, including ``BaseException``, and reports the failure on its result.
The caller is on the post-close path, so a raise here would propagate into the close and
break §2's guarantee that *"if this component fails entirely, the close is unaffected and
the meeting record is identical."* The broad catch is the point, not an oversight: a narrow
one would let an unforeseen error class through into the close, which is the exact harm.

Nothing here writes the notes object, and nothing holds the bot in the meeting. The close
record is passed in read-only and is never handed to anything that could mutate it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .clarify import ClarifyResult, run_clarify
from .config import PostMeetingConfig, load_post_meeting_config
from .extract import ExtractResult, run_extract
from .models import Source, TaskState, Tier
from .plan import PlanResult, run_plan
from .triage import TriageResult, run_triage

log = logging.getLogger(__name__)


@dataclass
class IntakeResult:
    """What intake produced, and honestly what it failed to produce."""

    extract: Optional[ExtractResult] = None
    triage: Optional[TriageResult] = None
    clarify: Optional[ClarifyResult] = None
    plans: list[PlanResult] = field(default_factory=list)
    error: Optional[BaseException] = None
    #: The stage that failed, when one did — for the log line and the report.
    failed_stage: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def task_count(self) -> int:
        return len(self.extract.tasks) if self.extract else 0


#: Tiers that get a plan written (Doc 07 §3.4: "for every surviving item").
#: informational and question never reach B4 — the first produces nothing, the second is
#: waiting on a human.
_PLANNED_TIERS = (Tier.TICKET, Tier.TICKET_PLAN, Tier.TICKET_PLAN_DRAFT)


async def run_intake(
    final_notes: Any,
    *,
    meeting_id: Any,
    tenant_id: Any,
    task_store: Any,
    clarify_store: Any,
    caller: Any,
    call_external: Any,
    channels: Sequence[str] = (),
    config: Optional[PostMeetingConfig] = None,
    source: Source = Source.CLOSE_ITEM,
) -> IntakeResult:
    """B1 → B2 → B3 → B4 over one close record. **Never raises.**

    Each stage is entered only if the previous one produced something to work on, and any
    stage's failure stops the pipeline without stopping the caller.
    """
    cfg = config if config is not None else load_post_meeting_config()
    result = IntakeResult()
    try:
        # ── B1 extract ────────────────────────────────────────────────────
        result.extract = await run_extract(
            final_notes, meeting_id=meeting_id, tenant_id=tenant_id,
            store=task_store, source=source,
        )
        if not result.extract.ok:
            result.error, result.failed_stage = result.extract.error, "extract"
            return result
        tasks = result.extract.tasks
        if not tasks:
            return result

        # ── B2 triage ─────────────────────────────────────────────────────
        items = [(t.item_ref, t.text) for t in tasks]
        result.triage = await run_triage(
            items, caller=caller, call_external=call_external, config=cfg
        )
        by_ref = {t.item_ref: t for t in tasks}
        for ref, verdict in result.triage.verdicts.items():
            task = by_ref.get(ref)
            if task is None:
                continue
            # The first and only caller of set_tier: triage's verdict is what moves a
            # task off EXTRACTED.
            await task_store.set_tier(task.task_id, verdict.tier, state=TaskState.TRIAGED)
            task.tier = verdict.tier
            task.state = TaskState.TRIAGED
        if not result.triage.ok:
            # Untiered items are left at EXTRACTED and are never planned (AC-PME-01-NEG).
            result.error, result.failed_stage = result.triage.error, "triage"
            return result

        # ── B3 clarify ────────────────────────────────────────────────────
        triaged = [t for t in tasks if t.tier is not None]
        result.clarify = await run_clarify(
            [
                {
                    "task_id": t.task_id,
                    "item_ref": t.item_ref,
                    "owner": t.owner,
                    "text": t.text,
                    # A question-tier verdict IS triage saying the item is underspecified.
                    "has_scope": t.tier is not Tier.QUESTION,
                    "has_done_condition": t.tier is not Tier.QUESTION,
                    "attributed_person": t.owner,
                }
                for t in triaged
            ],
            tenant_id=tenant_id, meeting_id=meeting_id,
            clarify_store=clarify_store, task_store=task_store, channels=channels,
        )
        held = {o.item_ref for o in result.clarify.outcomes}

        # ── B4 plan ───────────────────────────────────────────────────────
        for task in triaged:
            if task.item_ref in held:
                continue  # ambiguity stopped the line (§3.3)
            if task.tier not in _PLANNED_TIERS:
                continue  # informational produces nothing
            result.plans.append(
                await run_plan(
                    task_id=task.task_id, text=task.text, owner=task.owner,
                    item_ref=task.item_ref, store=task_store,
                    caller=caller, call_external=call_external,
                )
            )
    except BaseException as exc:  # noqa: BLE001 - see module docstring; the close must
        # never see an exception from this component, whatever its class.
        log.exception("post-meeting intake failed for meeting %s", meeting_id)
        result.error = exc
        result.failed_stage = result.failed_stage or "intake"
    return result


async def run_intake_guarded(*args: Any, **kwargs: Any) -> Optional[IntakeResult]:
    """The close path's entry point. Swallows even a failure to *construct* the result.

    :func:`run_intake` already cannot raise, but this is the seam the close calls, and the
    close's guarantee must not depend on that remaining true as intake grows. Returns
    ``None`` if intake could not run at all.
    """
    try:
        return await run_intake(*args, **kwargs)
    except BaseException:  # noqa: BLE001 - defence in depth for §2
        log.exception("post-meeting intake could not run; close is unaffected")
        return None
