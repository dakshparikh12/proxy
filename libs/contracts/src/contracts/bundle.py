"""Bundle (04->05): the ask handed from Orchestrator to Workroom.

AC-CMP-002. ``notes_ref`` is a UUID handle, never an embedded notes object
(Truth-is-live: the notes object is fetched fresh, never carried).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class Bundle(BaseModel):
    ask: str
    speaker: str
    timestamp: datetime
    notes_ref: UUID
    transcript_tail: list[str] = Field(default_factory=list)
    task_id: UUID
