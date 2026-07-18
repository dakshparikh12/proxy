"""Material-change events (03→04 contract, AC-CMP-017).

The Scribe emits one of exactly these seven kinds on delta application (chitchat
emits nothing); Doc 04 consumes the stream. Per 00-FOUNDATION.md:46 the enum is
CLOSED — the dropped 'note'-style shorthand is not a member, and decision and
question are the expanded forming/final and open/closed variants, never single
combined members.
"""
from __future__ import annotations

from enum import StrEnum


class MaterialChangeKind(StrEnum):
    """The closed set of material-change event kinds (03→04)."""

    CLAIM_LANDED_CHECKABLE = "claim-landed-checkable"
    DECISION_FORMING = "decision-forming"
    DECISION_FINAL = "decision-final"
    CONTRADICTION = "contradiction"
    ACTION_ITEM = "action-item"
    QUESTION_OPEN = "question-open"
    QUESTION_CLOSED = "question-closed"
