"""B3 — clarify. Ambiguity stops the line.

Criteria: **AC-PME-03, AC-PME-03-NEG** (an item lacking owner, scope, or a done-condition
is never planned and becomes a clarifying question) and **AC-PME-04, AC-PME-04-NEG** (a
question with no attributed person or no channel stays pending on the task record).

Doc 07 §3.3. Two things about this module are load-bearing:

**Routing is mechanical, not judged.** *"routing is mechanical, not judged: the follow-up
goes to the person the notes attribute the item to — its speaker or its named owner."*
There is no model call here. :func:`route_question` is a lookup over what the notes already
say, and it returns ``None`` rather than picking someone plausible.

**This is the ONE table writable before APPROVED.** Doc 07 §3.4's invariant is that no
durable write happens outside the task's own record before a named human approves — and
``clarify_items`` is the single closed carve-out (founder ruling C-D, recorded in the spec
and in migration 0010). Asking a question is not a world-change. AC-PME-07 pins the
permitted pre-approval write set to exactly ``{post_meeting_tasks, clarify_items}``, so
anything added here that writes a third table fails that criterion.

Failure is fail-CLOSED. If the clarify write fails, the item is still not planned
(AC-PME-03-NEG): failing open would plan an item nobody scoped, which is the precise harm
§3.3 exists to prevent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

from .models import UNRESOLVED, TaskState

log = logging.getLogger(__name__)


class ClarifyStore(Protocol):
    async def insert(
        self,
        *,
        tenant_id: Any,
        meeting_id: Any,
        question: str,
        kind: Optional[str] = None,
        blocking_ref: Optional[str] = None,
        urgency: Optional[str] = None,
    ) -> Any: ...


@dataclass(frozen=True)
class Ambiguity:
    """What the item is missing. Doc 07 §3.3 names exactly these three."""

    missing_owner: bool
    missing_scope: bool
    missing_done_condition: bool

    @property
    def any(self) -> bool:
        return self.missing_owner or self.missing_scope or self.missing_done_condition

    def describe(self) -> str:
        parts = []
        if self.missing_owner:
            parts.append("who owns it")
        if self.missing_scope:
            parts.append("what exactly it covers")
        if self.missing_done_condition:
            parts.append("what would make it done")
        return " and ".join(parts)


@dataclass
class ClarifyOutcome:
    """One item's trip through B3."""

    item_ref: str
    #: True when a clarify_items row was durably written.
    written: bool = False
    clarify_id: Any = None
    #: Where the question went. None = nowhere; it stays pending on the task record.
    routed_to: Optional[str] = None
    #: True when the question could not be delivered and surfaces on the draft card.
    pending: bool = False
    error: Optional[BaseException] = None
    question: str = ""


@dataclass
class ClarifyResult:
    outcomes: list[ClarifyOutcome] = field(default_factory=list)

    @property
    def pending(self) -> list[ClarifyOutcome]:
        return [o for o in self.outcomes if o.pending]


def assess(
    *, owner: str, text: str, has_scope: Optional[bool] = None,
    has_done_condition: Optional[bool] = None,
) -> Ambiguity:
    """Which of the three §3.3 signals are missing.

    ``has_scope`` / ``has_done_condition`` come from triage (the model's read of the item),
    because "does this have a scope" is judgement. Ownership is NOT judgement — it is
    ``owner == UNRESOLVED``, decided in B1 and never re-derived here.

    Passing ``None`` for either judged signal means "triage did not say", which is treated
    as MISSING. An unknown scope is not a scope; the safe direction is to ask.
    """
    return Ambiguity(
        missing_owner=(owner == UNRESOLVED or not owner.strip()),
        missing_scope=(has_scope is not True),
        missing_done_condition=(has_done_condition is not True),
    )


def compose_question(text: str, ambiguity: Ambiguity) -> str:
    """One question naming exactly what is missing. Doc 07 §5's shape."""
    return f"About “{text}” — {ambiguity.describe()}?"


def route_question(
    *,
    attributed_person: Optional[str],
    channels: Sequence[str],
) -> Optional[str]:
    """Mechanical routing (§3.3). Returns the recipient, or ``None`` to hold.

    ``None`` is returned when there is no person the notes attribute the item to, or no
    out-of-meeting channel exists. It is never a fallback recipient: AC-PME-04-NEG asserts
    no channel is invented and no message is sent, and picking "someone senior" here would
    be the ownership inference §3.2 forbids, one layer down.
    """
    if not attributed_person or not attributed_person.strip():
        return None
    if attributed_person.strip() == UNRESOLVED:
        return None
    if not channels:
        return None
    return attributed_person.strip()


async def run_clarify(
    items: Sequence[dict[str, Any]],
    *,
    tenant_id: Any,
    meeting_id: Any,
    clarify_store: ClarifyStore,
    task_store: Any,
    channels: Sequence[str] = (),
) -> ClarifyResult:
    """Raise a clarifying question per ambiguous item and hold the item.

    ``items`` are dicts carrying ``task_id``, ``item_ref``, ``owner``, ``text`` and the
    optional judged signals ``has_scope`` / ``has_done_condition``.

    The task moves to ``CLARIFYING`` and — critically — **never gets a plan**, whether or
    not the clarify write or the routing succeeded.
    """
    result = ClarifyResult()
    for item in items:
        ref = str(item.get("item_ref", ""))
        ambiguity = assess(
            owner=str(item.get("owner", UNRESOLVED)),
            text=str(item.get("text", "")),
            has_scope=item.get("has_scope"),
            has_done_condition=item.get("has_done_condition"),
        )
        if not ambiguity.any:
            continue

        question = compose_question(str(item.get("text", "")), ambiguity)
        outcome = ClarifyOutcome(item_ref=ref, question=question)

        # Hold the item FIRST. If the clarify write then fails, the item is already
        # held — fail-closed (AC-PME-03-NEG). Ordering here is the guarantee.
        try:
            await task_store.set_state(item["task_id"], TaskState.CLARIFYING)
        except Exception as exc:  # noqa: BLE001 - the item must still not be planned
            log.exception("could not move %s to CLARIFYING", ref)
            outcome.error = exc
            outcome.pending = True
            result.outcomes.append(outcome)
            continue

        try:
            outcome.clarify_id = await clarify_store.insert(
                tenant_id=tenant_id,
                meeting_id=meeting_id,
                question=question,
                kind="post-meeting",
                blocking_ref=ref,
                urgency="high" if ambiguity.missing_owner else "normal",
            )
            outcome.written = True
        except Exception as exc:  # noqa: BLE001 - surfaced, never treated as asked
            log.exception("clarify_items write failed for %s", ref)
            outcome.error = exc
            outcome.pending = True
            result.outcomes.append(outcome)
            continue

        recipient = route_question(
            attributed_person=item.get("attributed_person") or item.get("owner"),
            channels=channels,
        )
        if recipient is None:
            # No attributed person or no channel: the question stays pending on the task
            # record and surfaces on the draft cards (Doc 08). Nothing is sent.
            outcome.pending = True
        else:
            outcome.routed_to = recipient
        result.outcomes.append(outcome)
    return result
