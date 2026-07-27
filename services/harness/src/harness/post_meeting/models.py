"""Doc 07 vocabulary — the state machine, the tiers, and UNRESOLVED.

Spec tokens, not paraphrases. Every string here appears verbatim in Doc 07 and in the
``post_meeting_tasks`` CHECK constraints (migration 0009), so a drift between the Python
enum and the database domain is a test failure rather than a runtime surprise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskState(str, Enum):
    """Doc 07 §3.9.

    ``EXTRACTED → TRIAGED → CLARIFYING → PLANNED → APPROVED → RUNNING → DRAFTED``
    then one of ``ACCEPTED | CHANGES_REQUESTED | DISCARDED``.

    ``APPROVED`` — never ``PLAN_APPROVED``. The spec token wins (§3.9); an earlier build
    brief used the other spelling and the tests assert this one.

    ``CHANGES_REQUESTED`` is the reviewer's "request changes" outcome, spelled in full so
    it is not mistaken for a diff-content state.
    """

    EXTRACTED = "EXTRACTED"
    TRIAGED = "TRIAGED"
    CLARIFYING = "CLARIFYING"
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    DRAFTED = "DRAFTED"
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    DISCARDED = "DISCARDED"


class Tier(str, Enum):
    """Doc 07 §3.1. Exactly one per action item, ordered least-to-most consequential.

    The order matters: ``drop_one`` (§3.1 "when in doubt, drop a tier") walks it, and
    AC-PME-06 asserts a draft-tier violation lands strictly below ``TICKET_PLAN_DRAFT``.
    """

    INFORMATIONAL = "informational"
    QUESTION = "question"
    TICKET = "ticket"
    TICKET_PLAN = "ticket+plan"
    TICKET_PLAN_DRAFT = "ticket+plan+draft"


#: Least → most consequential. Index is the tier's rank.
TIER_ORDER: tuple[Tier, ...] = (
    Tier.INFORMATIONAL,
    Tier.QUESTION,
    Tier.TICKET,
    Tier.TICKET_PLAN,
    Tier.TICKET_PLAN_DRAFT,
)


def drop_one_tier(tier: Tier) -> Tier:
    """Doc 07 §3.1: "When in doubt, drop a tier."

    Saturates at ``INFORMATIONAL`` — there is nothing below it, and inventing a sixth
    tier to represent "even less" is exactly the drift §3.1 warns about.
    """
    idx = TIER_ORDER.index(tier)
    return TIER_ORDER[max(0, idx - 1)]


class Source(str, Enum):
    """Where the item came from (Doc 07 §3.1 intake). Matches migration 0009's CHECK."""

    CLOSE_ITEM = "close-item"
    DOC06_WORK = "doc06-work"


#: Doc 07 §3.2. A REAL owner value, deliberately distinct from empty and from NULL.
#: "An owner comes from the room or the item is UNRESOLVED." It is what stops fake
#: ownership inference, and it holds the item at the question tier.
UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ExtractedItem:
    """One action item lifted from the close output, before any judgement is applied.

    Deliberately carries no tier: B1 extracts, B2 tiers. An item leaves B1 at
    ``EXTRACTED`` with ``tier=None``.
    """

    item_ref: str
    text: str
    owner: str
    source: Source
    #: True when the close output named an owner; False when we fell back to UNRESOLVED.
    owner_from_room: bool


@dataclass
class TaskRecord:
    """A ``post_meeting_tasks`` row, as the application sees it."""

    task_id: Any
    tenant_id: Any
    meeting_id: Any
    source: Source
    item_ref: str
    state: TaskState = TaskState.EXTRACTED
    tier: Optional[Tier] = None
    owner: str = UNRESOLVED
    plan: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[Any] = None
    operation_ref: Optional[Any] = None
    draft_id: Optional[Any] = None
    cost_usd: float = 0.0
    outcome: Optional[str] = None
    #: Not persisted — the item text, carried in memory for triage/planning.
    text: str = ""
    #: Not persisted — degradation observed while reading the notes (Doc 07 Law 1 /
    #: AC-PME-05-NEG): a degraded read is recorded, never presented as a confident one.
    read_degraded: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
