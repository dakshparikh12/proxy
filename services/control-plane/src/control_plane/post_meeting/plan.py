"""B4 — plan. Write the plan onto the task record, then wait for a human.

Criteria: **AC-PME-08, AC-PME-08-NEG** (an unanswered plan expires quietly without
proceeding).

Doc 07 §3.4. The plan is nine fields — *"the task in one line · why it exists, with the
meeting reference · the owner · assumptions · risks · what 'done' looks like · the files
it expects to touch · the steps · a confidence signal"* — produced by one structured call
on the same sonnet seat as triage, and written to ``post_meeting_tasks.plan``. The plan
text lives on that record and nowhere else (§3.4), which is what keeps a pre-approval task
inside the permitted write set.

**Expiry is quiet.** §3.4: *"A plan nobody answers expires quietly after ``plan_expiry``.
Proxy does not nag and never proceeds by default."* So :func:`expire_stale_plans` closes
the task and sends nothing. There is deliberately no reminder, no second notification, and
no "assume yes" path — the absence of an answer is an answer.

Clock handling is explicit: ``now`` is injected. AC-PME-08-NEG drives a backward clock and
a crashed sweep, and a module reading the wall clock directly could not be tested against
either.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from libs.llm.src.llm.structured import (
    CallExternal,
    StructuredCaller,
    StructuredOutputError,
    generate_structured,
)

from .config import PostMeetingConfig, load_post_meeting_config
from .models import TaskState

log = logging.getLogger(__name__)

PLAN_SEAT = "ORCHESTRATOR"  # same seat as triage; see triage.TRIAGE_SEAT for why
_TOOL_NAME = "emit_plan"

#: Doc 07 §3.4's nine fields, in the order the spec lists them.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_one_line": {"type": "string"},
        "why_it_exists": {"type": "string"},
        "meeting_reference": {"type": "string"},
        "owner": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "done_looks_like": {"type": "string"},
        "files_expected": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "task_one_line",
        "why_it_exists",
        "meeting_reference",
        "owner",
        "done_looks_like",
        "steps",
        "confidence",
    ],
}

#: What the owner may do with a plan (§3.4).
PLAN_RESPONSES = ("approve", "edit", "split", "downgrade", "reject")


@dataclass
class PlanResult:
    task_id: Any = None
    plan_text: Optional[str] = None
    written: bool = False
    error: Optional[BaseException] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.written


@dataclass
class ExpirySweepResult:
    """What one expiry sweep did. Re-runnable: a crashed sweep leaves this consistent."""

    expired: list[Any] = field(default_factory=list)
    skipped: list[Any] = field(default_factory=list)
    notifications_sent: int = 0
    errors: list[BaseException] = field(default_factory=list)


def render_plan(data: dict[str, Any]) -> str:
    """Render the nine fields as the plan text stored on the task record."""
    def _lines(key: str) -> str:
        vals = data.get(key) or []
        if not isinstance(vals, list):
            return ""
        return "\n".join(f"  - {v}" for v in vals if isinstance(v, str))

    parts = [
        f"TASK: {data.get('task_one_line', '')}",
        f"WHY: {data.get('why_it_exists', '')} ({data.get('meeting_reference', '')})",
        f"OWNER: {data.get('owner', '')}",
        f"DONE WHEN: {data.get('done_looks_like', '')}",
        f"CONFIDENCE: {data.get('confidence', '')}",
    ]
    for label, key in (
        ("ASSUMPTIONS", "assumptions"),
        ("RISKS", "risks"),
        ("FILES EXPECTED", "files_expected"),
        ("STEPS", "steps"),
    ):
        body = _lines(key)
        if body:
            parts.append(f"{label}:\n{body}")
    return "\n".join(parts)


def build_prompt(*, text: str, owner: str, item_ref: str) -> str:
    return "\n".join(
        [
            "Write a plan for this action item from a meeting that has just ended.",
            "A named human will read it and approve, edit, split, downgrade or reject it.",
            "Nothing runs until they approve, so be honest about assumptions and risks.",
            "",
            f"ITEM: {text}",
            f"OWNER: {owner}",
            f"MEETING REFERENCE: {item_ref}",
        ]
    )


async def run_plan(
    *,
    task_id: Any,
    text: str,
    owner: str,
    item_ref: str,
    store: Any,
    caller: StructuredCaller,
    call_external: CallExternal,
    seat: str = PLAN_SEAT,
) -> PlanResult:
    """Produce the plan and persist it, moving the task to PLANNED.

    On any failure the task does NOT move to PLANNED — an item with no plan must not look
    like an item awaiting approval.
    """
    result = PlanResult(task_id=task_id)
    try:
        structured = await generate_structured(
            seat=seat,
            prompt=build_prompt(text=text, owner=owner, item_ref=item_ref),
            output_schema=PLAN_SCHEMA,
            caller=caller,
            call_external=call_external,
            tool_name=_TOOL_NAME,
        )
    except StructuredOutputError as exc:
        log.warning("plan call failed for %s: %s", item_ref, exc)
        result.error = exc
        return result

    result.plan_text = render_plan(structured.data)
    try:
        await store.set_plan(task_id, result.plan_text, state=TaskState.PLANNED)
        result.written = True
    except Exception as exc:  # noqa: BLE001 - never leave a half-planned task advancing
        log.exception("could not persist plan for %s", item_ref)
        result.error = exc
    return result


def is_expired(
    *, planned_at: datetime, now: datetime, plan_expiry_hours: int
) -> bool:
    """Whether a plan has outlived ``plan_expiry``.

    A ``now`` earlier than ``planned_at`` (clock skew backward) is NOT expired — and
    critically, it also cannot *un*-expire a task that already reached a terminal state,
    because the sweep only ever reads non-terminal tasks (AC-PME-08-NEG).
    """
    if now < planned_at:
        return False
    return (now - planned_at) >= timedelta(hours=plan_expiry_hours)


def _state_value(state: Any) -> Optional[str]:
    """Normalise a state that may arrive as a ``TaskState`` or as its raw string.

    A row read straight from asyncpg carries the text; a row built in memory carries the
    enum. Returning ``None`` for anything else makes an unreadable state fall through to
    "not PLANNED", which is the safe direction — the sweep skips it rather than expiring
    a task whose state it could not determine.
    """
    if isinstance(state, TaskState):
        return state.value
    if isinstance(state, str):
        return state
    return None


async def expire_stale_plans(
    tasks: Sequence[dict[str, Any]],
    *,
    store: Any,
    now: Optional[datetime] = None,
    config: Optional[PostMeetingConfig] = None,
) -> ExpirySweepResult:
    """Close unanswered plans quietly. Sends nothing. Safe to re-run after a crash.

    ``tasks`` are dicts with ``task_id``, ``state`` and ``planned_at``. Only tasks still
    at ``PLANNED`` are considered: a task that already moved on — approved, running,
    discarded — is skipped, which is what makes a re-run after a mid-sweep crash a no-op
    on the rows the first pass already closed, and what stops a backward clock from
    reopening anything.
    """
    cfg = config if config is not None else load_post_meeting_config()
    when = now if now is not None else datetime.now(timezone.utc)
    out = ExpirySweepResult()

    for row in tasks:
        if _state_value(row.get("state")) != TaskState.PLANNED.value:
            out.skipped.append(row.get("task_id"))
            continue
        planned_at = row.get("planned_at")
        if not isinstance(planned_at, datetime):
            out.skipped.append(row.get("task_id"))
            continue
        if not is_expired(
            planned_at=planned_at, now=when, plan_expiry_hours=cfg.plan_expiry_hours
        ):
            out.skipped.append(row.get("task_id"))
            continue
        try:
            await store.set_outcome(
                row["task_id"],
                state=TaskState.DISCARDED,
                outcome="expired: plan unanswered after "
                f"{cfg.plan_expiry_hours}h; closed without proceeding",
            )
            out.expired.append(row["task_id"])
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the sweep
            log.exception("expiry sweep failed for task %s", row.get("task_id"))
            out.errors.append(exc)
    # notifications_sent stays 0 by construction: Proxy does not nag (§3.4).
    return out
