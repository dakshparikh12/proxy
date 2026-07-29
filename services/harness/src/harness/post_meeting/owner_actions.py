"""§3.4 owner actions — what the owner may do with a plan besides approve.

Doc 07 §3.4: *"The owner may **approve · edit · split · downgrade to a ticket · reject.**"*
Only ``approve`` existed (B5 + SEAM 2). This adds **reject**, **downgrade to a ticket** and
**edit**. ``split`` is **deferred** — see the note at the bottom of this module and the
§3.4 deferral recorded in the spec.

All three act on a task at ``PLANNED`` — a plan sitting in front of a human. Each refuses
from any other state, for the same reason ``approve`` does: there is nothing to decide
about a task that is still being triaged, or one that is already running.

None of them is a world-touching act, so none needs the approval gate's ceremony. What
they share with ``approve`` is that a **named human** performs them, and each records who.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .approval import is_named_human
from .models import TaskState, Tier

log = logging.getLogger(__name__)


class OwnerActionRefused(Exception):
    """The action was not applicable. Carries why, so a route can report it honestly."""


@dataclass(frozen=True)
class OwnerAction:
    """One recorded owner decision."""

    action: str
    task_id: Any
    by: str
    new_state: TaskState
    detail: str = ""


def _require(state: Any, actor: Any, action: str) -> str:
    """Shared precondition: a named human acting on a PLANNED task."""
    if not is_named_human(actor):
        raise OwnerActionRefused(f"{action} requires a named human; got {actor!r}")
    value = state.value if isinstance(state, TaskState) else state
    if value != TaskState.PLANNED.value:
        raise OwnerActionRefused(
            f"{action} applies to a PLANNED task; this one is {value!r}"
        )
    return str(actor).strip()


async def reject(
    *, task_id: Any, actor: Any, current_state: Any, store: Any
) -> OwnerAction:
    """The owner rejects the plan. The task is DISCARDED and nothing runs.

    Distinct from ``CHANGES_REQUESTED`` (SEAM 3), which is a reviewer asking for another
    pass on a draft that already exists. A rejection here means the work should not happen
    at all, so the task is finished — DISCARDED, terminal, with the rejector named.
    """
    who = _require(current_state, actor, "reject")
    await store.set_outcome(
        task_id,
        state=TaskState.DISCARDED,
        outcome=f"rejected by {who}: the plan was declined; no work was started",
    )
    return OwnerAction(
        action="reject", task_id=task_id, by=who, new_state=TaskState.DISCARDED,
        detail="plan declined",
    )


async def downgrade_to_ticket(
    *, task_id: Any, actor: Any, current_state: Any, store: Any
) -> OwnerAction:
    """The owner keeps the item but wants no automation: it becomes a plain ticket.

    Per §3.1 the ``ticket`` tier means *"a human should do this"* and produces *"a staged
    task record"* — so the item stays alive and visible, but there is nothing to approve
    and nothing to dispatch.

    Implemented without inventing a state: the tier drops to ``ticket`` and the task
    returns to ``TRIAGED``, which takes it out of the PLANNED expiry sweep (there is no
    plan awaiting an answer) and out of dispatch (``ticket`` is not a dispatchable tier).
    The plan text and the expiry clock are cleared — keeping a stale plan on a downgraded
    ticket would leave a document nobody is going to act on.
    """
    who = _require(current_state, actor, "downgrade")
    await store.downgrade_to_ticket(
        task_id,
        outcome=f"downgraded to a ticket by {who}: recorded for a human, not automated",
    )
    return OwnerAction(
        action="downgrade", task_id=task_id, by=who, new_state=TaskState.TRIAGED,
        detail=f"tier -> {Tier.TICKET.value}",
    )


async def edit_plan(
    *, task_id: Any, actor: Any, current_state: Any, new_plan: str, store: Any
) -> OwnerAction:
    """The owner rewrites the plan. It stays PLANNED and still needs a fresh approval.

    Two things make this safe rather than a way around the gate:

    * **No approval is granted or carried over.** The task remains ``PLANNED``; the owner
      must still approve, and ``approved_by``/``approved_at`` are untouched (they are NULL
      at ``PLANNED`` by construction — migration 0009's CHECK).
    * **The expiry clock restarts**, because ``store.set_plan`` re-stamps ``planned_at``.
      An edited plan is a new plan awaiting a new decision; inheriting the remains of the
      previous window would expire a plan the owner had just engaged with.

    An empty edit is refused — silently clearing a plan is not an edit.
    """
    who = _require(current_state, actor, "edit")
    if not isinstance(new_plan, str) or not new_plan.strip():
        raise OwnerActionRefused("edit requires plan text; an empty edit is not an edit")
    await store.set_plan(task_id, new_plan.strip(), state=TaskState.PLANNED)
    return OwnerAction(
        action="edit", task_id=task_id, by=who, new_state=TaskState.PLANNED,
        detail="plan rewritten; expiry clock restarted; approval still required",
    )


# ── split: DEFERRED (founder call, 2026-07-29) ────────────────────────────────
#
# §3.4 lists ``split`` among the owner's actions. It is deliberately NOT built, and it is
# the only one of the five that is not, because it is the only one that is not a state
# write. Splitting one task into N needs:
#
#   * a parent/child relation `post_meeting_tasks` has no column for — a new migration,
#     and a decision about whether the parent stays non-terminal while its children run
#     (if it does, it holds a dispatch slot; if it does not, the children have no owner
#     record above them);
#   * `max_tasks_per_meeting` accounting across the split — one task becoming four either
#     consumes four slots or the cap stops meaning what it says;
#   * a rule for what happens when children disagree: three ACCEPTED and one
#     CHANGES_REQUESTED is not obviously any single parent outcome.
#
# Estimated 1–1.5 days with the migration, and it would want its own commit. Nobody has
# asked for it and there are no users yet, so the cost is not worth paying to satisfy a
# bullet. An owner who wants a split can reject the plan and let the next meeting produce
# the pieces — or edit the plan down to the part that is actually startable.
#
# Recorded in the spec at §3.4 so this is a decision on the record rather than an omission.
