"""SEAM 3 — a human's accept / request-changes closes the post-meeting task.

Doc 07 §3.9's terminal states are ``ACCEPTED | CHANGES_REQUESTED | DISCARDED``. Before
this seam the first two were unreachable: nothing wrote them, so a task that produced a
draft stayed at ``DRAFTED`` forever no matter what the human did with it.

The trigger is Doc 04's accept route (`control_plane/accept_route.py`), which owns the
draft's own lifecycle — flipping ``staged_drafts.status`` to ``applied`` or ``rejected``.
**That is the draft's state, not the task's.** This module writes the *task* side:
``post_meeting_tasks.outcome`` plus the terminal state, found by ``draft_id``.

Two rules hold it in place:

* **The draft lifecycle wins.** The route does its own work first and this is called
  afterwards, so a failure here can never prevent a human's accept from landing. Like
  SEAM 1, the call is total.
* **Doc 07 does not write ``staged_drafts``** (§3.8). Nothing here touches that table; it
  reads a ``draft_id`` the route already resolved and writes only Doc 07's own record.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .models import TaskState

log = logging.getLogger(__name__)

#: Doc 07 §3.9: "CHANGES_REQUESTED is the reviewer's 'request changes' outcome — spelled
#: in full so no builder mistakes it for a diff-content state."
_ACCEPT_OUTCOME = "accepted by {who}: staged draft applied"
_CHANGES_OUTCOME = "changes requested by {who}: draft declined, task not discarded"


async def record_accept(
    *, draft_id: Any, store: Any, who: Optional[str] = None
) -> Optional[Any]:
    """A human accepted the draft → the task reaches ``ACCEPTED``. Never raises."""
    return await _record(
        draft_id=draft_id, store=store, state=TaskState.ACCEPTED,
        outcome=_ACCEPT_OUTCOME.format(who=who or "a named human"), label="accept",
    )


async def record_changes_requested(
    *, draft_id: Any, store: Any, who: Optional[str] = None
) -> Optional[Any]:
    """A human requested changes → ``CHANGES_REQUESTED``. Never raises.

    Deliberately NOT ``DISCARDED``. Requesting changes is a reviewer asking for another
    pass; discarding is the task being abandoned. Collapsing them would lose the
    distinction §3.9 spells out in full precisely so it is not lost.
    """
    return await _record(
        draft_id=draft_id, store=store, state=TaskState.CHANGES_REQUESTED,
        outcome=_CHANGES_OUTCOME.format(who=who or "a named human"), label="reject",
    )


async def _record(
    *, draft_id: Any, store: Any, state: TaskState, outcome: str, label: str
) -> Optional[Any]:
    """Find the task by ``draft_id`` and write its terminal outcome.

    Total by construction: the caller is Doc 04's accept route, which has already applied
    or declined the draft on durable storage by the time this runs. A fault here must not
    turn a successful human action into an error response.
    """
    try:
        task_id = await store.task_id_for_draft(draft_id)
        if task_id is None:
            # A draft with no post-meeting task is entirely normal — the live in-meeting
            # path stages drafts too, and those have no Doc 07 record.
            return None
        await store.set_outcome(task_id, state=state, outcome=outcome)
        return task_id
    except BaseException:  # noqa: BLE001 - a human's accept has already landed; this
        # write-back is bookkeeping and must never fail it.
        log.exception(
            "post-meeting %s write-back failed for draft %s; the draft action stands",
            label, draft_id,
        )
        return None
