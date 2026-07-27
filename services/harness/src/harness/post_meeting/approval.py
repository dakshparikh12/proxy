"""B5 — the approval gate. The safety boundary of Doc 07.

Criteria: **AC-PME-07, AC-PME-07-NEG**.

Doc 07 §3.4: *"Until that approval exists, no sandbox starts, no model does work beyond
triage and the plan itself, and no durable write occurs outside the task's own record."*
This is Law 3 and Invariant 6 applied to the **run**: Invariant 6 already covers the
artifact at the end; this covers the start.

Four properties, each enforced somewhere it cannot be forgotten:

1. **APPROVED requires a named human.** :func:`approve` refuses an empty approver, and the
   database refuses it independently — migration 0009's CHECK
   ``state <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)``.
   AC-PME-07-NEG asserts the database rejects it "independently of application code", so
   the application check is the fast path, not the guarantee.

2. **RUNNING is entered only from APPROVED.** :func:`may_dispatch` is the application
   check; migration 0009's BEFORE INSERT OR UPDATE trigger is the guarantee.

3. **The permitted pre-approval write set is closed** — exactly
   ``{post_meeting_tasks, clarify_items}`` (§3.4 as amended by ruling C-D).
   :data:`PRE_APPROVAL_WRITABLE_TABLES` is that set, and it is the value the tests assert
   against so a future block adding a third table fails loudly.

4. **The gate fails CLOSED.** Every ambiguity — an unreadable approval row, a lookup that
   errored, an ``APPROVED`` row missing an approver field — resolves to "not approved".
   A gate that fails open under substrate error is not a gate.

The gate never *performs* the approval decision; a named human does. What lives here is
the check that the decision happened, and the refusal to proceed without it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .models import TaskState

log = logging.getLogger(__name__)

#: The ONLY tables writable before APPROVED (Doc 07 §3.4 + ruling C-D). Closed set:
#: clarify_items is exempt because asking a question is not a world-change.
PRE_APPROVAL_WRITABLE_TABLES: frozenset[str] = frozenset(
    {"post_meeting_tasks", "clarify_items"}
)

#: What a task may have spent a model call on before approval (§3.4).
PRE_APPROVAL_MODEL_CALLS: frozenset[str] = frozenset({"triage", "plan"})

#: States from which a task may still legitimately reach APPROVED.
_APPROVABLE_FROM: frozenset[str] = frozenset({TaskState.PLANNED.value})


class ApprovalRefused(Exception):
    """The gate said no. Carries why, so the refusal can be reported honestly."""


@dataclass(frozen=True)
class Approval:
    """A recorded human decision. Both fields are required — that is the point."""

    approved_by: str
    approved_at: datetime


def _state_value(state: Any) -> Optional[str]:
    if isinstance(state, TaskState):
        return state.value
    if isinstance(state, str):
        return state
    return None


def is_named_human(approver: Any) -> bool:
    """A named human is a non-empty string that is not a placeholder.

    ``UNRESOLVED`` is explicitly not an approver: it is the value that means "nobody was
    named", and letting it approve would launder the exact ambiguity §3.2 exists to hold.
    """
    if not isinstance(approver, str):
        return False
    name = approver.strip()
    if not name:
        return False
    return name.upper() not in {"UNRESOLVED", "NONE", "NULL", "SYSTEM", "PROXY"}


def is_approved(row: Any) -> bool:
    """Whether a task row represents a real, complete approval. Fails CLOSED.

    Anything that is not unambiguously an approval — a non-dict, a missing field, a null
    approver, an unreadable state — is not an approval.
    """
    if not isinstance(row, dict):
        return False
    if _state_value(row.get("state")) != TaskState.APPROVED.value:
        return False
    if not is_named_human(row.get("approved_by")):
        return False
    if row.get("approved_at") is None:
        return False
    return True


def may_dispatch(row: Any) -> bool:
    """RUNNING is entered only from APPROVED (§3.9). The application-side check."""
    return is_approved(row)


async def approve(
    *,
    task_id: Any,
    approver: Any,
    store: Any,
    now: Optional[datetime] = None,
    current_state: Any = None,
) -> Approval:
    """Record a named human's approval. Raises :class:`ApprovalRefused` otherwise.

    The write is ONE statement setting state, approver and timestamp together
    (``store.approve``), so a row can never exist at APPROVED without its approver even
    transiently — the database CHECK would reject that intermediate state anyway.
    """
    if not is_named_human(approver):
        raise ApprovalRefused(
            f"approval requires a named human; got {approver!r}"
        )
    if current_state is not None:
        state = _state_value(current_state)
        if state not in _APPROVABLE_FROM:
            raise ApprovalRefused(
                f"a task may only be approved from PLANNED; it is at {state!r}"
            )
    when = now if now is not None else datetime.now(timezone.utc)
    await store.approve(task_id, approved_by=approver.strip(), approved_at=when)
    return Approval(approved_by=approver.strip(), approved_at=when)


async def load_and_check(
    *, task_id: Any, store: Any
) -> tuple[bool, Optional[BaseException]]:
    """Read the task and decide whether it may run. Never raises.

    A lookup that errors returns ``(False, exc)`` — the gate stays shut and the reason is
    surfaced rather than resolved in favour of proceeding (AC-PME-07-NEG).
    """
    try:
        row = await store.get(task_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable gate is a CLOSED gate
        log.exception("approval lookup failed for task %s; refusing to dispatch", task_id)
        return False, exc
    return is_approved(row), None
