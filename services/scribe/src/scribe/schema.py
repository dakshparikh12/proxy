"""Note-extraction schema — the Pydantic source of truth for structured output.

Every note the Scribe emits is schema-shaped at generation (the tool's JSON
Schema is derived from these models) and schema-validated on receipt (the same
models re-validate the tool_use payload). This module is that single source of
truth: the enums, the entry models with their caps, the delta operations, and
the top-level ``NoteDelta``.

Two enforcement points go beyond a plain field cap and are called out where they
live:

* ``said_at_s`` carries *meeting-relative* seconds (from the transcript window),
  never a wall-clock unix epoch. A field validator rejects any value above the
  plausible meeting-duration ceiling so a laundered wall-clock timestamp cannot
  masquerade as a meeting offset.
* ``verified`` defaults to ``False`` on every ``Claim`` — a checkable claim lands
  UNVERIFIED; nothing in the extraction path sets it True at construction.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# Upper bound (seconds) for a meeting-relative offset. A real meeting window is
# on the order of an hour; 24h of meeting-relative seconds is an absurdly generous
# ceiling that still sits ~5 orders of magnitude below any unix epoch, so a
# wall-clock timestamp (e.g. 1_700_000_000.0) is unambiguously detected.
_MEETING_RELATIVE_SECONDS_CEILING: float = 86_400.0


class Firmness(str, Enum):
    firm = "firm"
    hedged = "hedged"
    speculative = "speculative"


class Provenance(str, Enum):
    # said vs concluded — blocks laundered hallucination.
    observed = "observed"
    inferred = "inferred"


class Reversibility(str, Enum):
    easy = "easy-to-reverse"
    hard = "hard-to-reverse"
    irreversible = "irreversible"


class DecisionStatus(str, Enum):
    forming = "forming"
    final = "final"


class Claim(BaseModel):
    kind: Literal["claim"] = "claim"
    text: str = Field(max_length=1000)
    speaker: str
    said_at_s: float  # meeting-relative seconds, from the window — never wall-clock
    firmness: Firmness
    provenance: Provenance
    verified: bool = False  # a checkable claim lands UNVERIFIED by default
    referents: list[str] = Field(default_factory=list, max_length=8)  # named things → code candidates
    contradicts: Optional[str] = None  # id of an existing claim it conflicts with

    @field_validator("said_at_s")
    @classmethod
    def _reject_wall_clock(cls, value: float) -> float:
        """Reject a wall-clock epoch masquerading as a meeting-relative offset."""
        if value < 0:
            raise ValueError("said_at_s must be a non-negative meeting-relative offset")
        if value > _MEETING_RELATIVE_SECONDS_CEILING:
            raise ValueError(
                "said_at_s must be meeting-relative seconds, not a wall-clock timestamp "
                f"(got {value}, ceiling {_MEETING_RELATIVE_SECONDS_CEILING})"
            )
        return value


class Decision(BaseModel):
    kind: Literal["decision"] = "decision"
    text: str = Field(max_length=1000)
    status: DecisionStatus
    reversibility: Reversibility  # downstream scales verification depth off THIS
    leans: dict[str, Literal["for", "against", "silent"]] = Field(default_factory=dict)  # speaker → stance
    referents: list[str] = Field(default_factory=list, max_length=8)


class ActionItem(BaseModel):
    kind: Literal["action"] = "action"
    text: str = Field(max_length=1000)
    owner: Optional[str] = None  # who
    due: Optional[str] = None  # when (as said, e.g. "by Fri")
    provenance: Provenance = Provenance.observed


class OpenQuestion(BaseModel):
    kind: Literal["open_question"] = "open_question"
    text: str = Field(max_length=1000)
    raised_by: Optional[str] = None
    positions: list[str] = Field(default_factory=list, max_length=12)  # a live debate held OPEN
    resolved: bool = False


class ContextLine(BaseModel):
    kind: Literal["context"] = "context"
    text: str = Field(max_length=500)  # minor color/chitchat gist — everything lands somewhere


Entry = Annotated[
    Union[Claim, Decision, ActionItem, OpenQuestion, ContextLine],
    Field(discriminator="kind"),
]


class AddOp(BaseModel):
    op: Literal["add"] = "add"
    entry: Entry


class PatchOp(BaseModel):
    op: Literal["patch"] = "patch"
    target_id: str  # existing entry id
    changes: dict[str, object]  # field→new value (forming→final; firmness up; question resolved…)
    supersede_reason: str = Field(max_length=300)  # the old value is kept superseded-not-erased (§3.6)


class CloseOp(BaseModel):
    op: Literal["close"] = "close"
    target_id: str  # existing entry id to resolve/close
    resolution: str = Field(max_length=300)  # how it closed (a question answered, a decision finalized)


class NoteDelta(BaseModel):
    """One micro-call's output — a DELTA, never a rewrite of the object."""

    ops: list[Union[AddOp, PatchOp, CloseOp]] = Field(default_factory=list, max_length=40)  # add | patch | close
    current_goal: Optional[str] = Field(default=None, max_length=200)  # the one-line goal/blocker signal


__all__ = [
    "Firmness",
    "Provenance",
    "Reversibility",
    "DecisionStatus",
    "Claim",
    "Decision",
    "ActionItem",
    "OpenQuestion",
    "ContextLine",
    "Entry",
    "AddOp",
    "PatchOp",
    "CloseOp",
    "NoteDelta",
]
