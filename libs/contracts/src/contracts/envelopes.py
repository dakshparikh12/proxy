"""Envelope (05->04) + EnvelopeStatus + the Workroom ProgressEvent variant.

AC-CMP-003/012/016. ``verification`` is an optional ``verified|unverified``
marker — deliberately NOT an ``EnvelopeStatus`` member.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

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


# ── the LIVE Envelope consumer reads are DERIVED, not hand-listed (§4.8 field-diff) ──
# The 05→04 result ``Envelope`` is rendered by the terminal-result consumers, which read
# ``status`` (finality gate), ``verification`` (grounded-vs-unverified, Law 2), ``draft_id``
# (the /m/ accept route), ``headline``/``detail``/``artifact``/``receipts`` — see
# control_plane/screen_modes.py:162-222 and transport/chat.py:349-356. Those attribute reads
# are DERIVED by the ``contracts.contract_reads`` AST sweep, not restated here, so a
# ``verified|draft``→``EnvelopeStatus`` drift (a consumer reading a bare ``verified``/``draft``
# the model never carries) shows up as a real consumed-but-never-produced orphan. ``task_id``
# is a construction-threaded correlation key no consumer reads by attribute — it is the one
# entry on the documented ``registry._FIELD_DIFF_ALLOWLIST``.
