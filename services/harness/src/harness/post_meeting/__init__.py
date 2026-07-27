"""Doc 07 — post-meeting execution.

Lives inside ``services/harness`` deliberately. Doc 07 §3.5 says the work runs in a
``meeting_runtime`` worker with no media session and adds **no new deployable**, and
sealed criterion AC-REPO-006 pins ``services/*`` to exactly five directories — so this is
a subpackage of the harness that already hosts ``meeting_runtime``, not a sixth service.

Build order (Doc 07 §3, and the order these modules may call each other):

    B1 extract → B2 triage → B3 clarify → B4 plan → B5 approval gate →
    B6 dispatch → B7 report → B8 final gate
"""
from __future__ import annotations

from .models import (
    TIER_ORDER,
    UNRESOLVED,
    ExtractedItem,
    Source,
    TaskRecord,
    TaskState,
    Tier,
    drop_one_tier,
)

__all__ = [
    "TIER_ORDER",
    "UNRESOLVED",
    "ExtractedItem",
    "Source",
    "TaskRecord",
    "TaskState",
    "Tier",
    "drop_one_tier",
]
