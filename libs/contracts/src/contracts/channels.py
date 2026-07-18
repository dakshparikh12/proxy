"""Channel report signal (AC-CMP-008). Field is named ``dm_available`` (bool)."""
from __future__ import annotations

from pydantic import BaseModel


class ChannelReport(BaseModel):
    dm_available: bool
