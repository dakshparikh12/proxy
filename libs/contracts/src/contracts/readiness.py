"""Readiness enum + report (AC-CMP-007). 'mapping' is Expansion, absent here."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Readiness = Literal["connecting", "cloning", "indexing", "ready", "not_ready"]


class ReadinessReport(BaseModel):
    status: Readiness
    coverage_pct: float = 0.0
    gaps: list[str] = Field(default_factory=list)
