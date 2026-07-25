"""Envelope (05->04) + EnvelopeStatus + the Workroom ProgressEvent variant.

AC-CMP-003/012/016. ``verification`` is an optional ``verified|unverified``
marker — deliberately NOT an ``EnvelopeStatus`` member.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .registry import register_field_consumer

EnvelopeStatus = Literal[
    "done", "partial", "failed", "needs_clarification", "needs_review"
]

if TYPE_CHECKING:
    # For the type checker the field is a plain optional literal.
    Verification = Literal["verified", "unverified"] | None
else:
    # At runtime the annotation is flattened so ``get_args`` yields the string
    # members alongside ``NoneType`` (AC-CMP-012), while still accepting ``None``.
    Verification = Literal["verified", "unverified"].copy_with(
        ("verified", "unverified", type(None), None)
    )


class Envelope(BaseModel):
    """The Workroom->Orchestrator result envelope."""

    headline: str
    detail: str | None = None
    artifact: dict[str, Any] | None = None
    receipts: list[str] = Field(default_factory=list)
    status: EnvelopeStatus
    verification: Verification = None
    draft_id: UUID | None = None
    task_id: UUID


class ProgressEvent(BaseModel):
    """A mid-task Workroom progress event: the Envelope shape minus finality.

    Carries no finalized terminal ``EnvelopeStatus`` (AC-CMP-016).
    """

    headline: str
    detail: str | None = None
    artifact: dict[str, Any] | None = None
    receipts: list[str] = Field(default_factory=list)
    task_id: UUID


# ── the LIVE Envelope consumers name the fields they read (§4.8 field-diff) ──
# The 05→04 result ``Envelope`` is built with every field set (workroom/envelope.py,
# harness/orchestrator.py:87) and the orchestrator renders the terminal result whole
# (``status`` gates finality, ``verification`` marks grounded-vs-unverified per Law 2,
# ``draft_id`` links the /m/ accept route, ``receipts`` carry the ✓-tagged citations).
# Naming the fields the REAL consumer reads is what makes the ``verified|draft``→
# ``EnvelopeStatus`` drift a build failure: a consumer reading a bare ``verified``/``draft``
# field would leave the real ``status``/``verification`` produced-but-unconsumed.
register_field_consumer(
    "Envelope",
    "headline",
    "detail",
    "artifact",
    "receipts",
    "status",
    "verification",
    "draft_id",
    "task_id",
)
