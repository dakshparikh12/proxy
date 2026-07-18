"""Notes delta ops (AC-CMP-006). 'note' is folded into 'add'."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

NoteOp = Literal["add", "patch", "close"]


class NoteDelta(BaseModel):
    op: NoteOp
    note_id: UUID | None = None
    body: dict[str, Any] = Field(default_factory=dict)
