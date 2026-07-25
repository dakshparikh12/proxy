"""Channel report signal (AC-CMP-008). Field is named ``dm_available`` (bool)."""
from __future__ import annotations

from pydantic import BaseModel

from .registry import register_field_consumer


class ChannelReport(BaseModel):
    dm_available: bool


# ── the LIVE channel-report consumer names the field it reads (§4.8 field-diff) ──
# ``services/transport/chat.py`` gates DM delivery on ``report.dm_available`` (chat.py:214)
# and ``services/transport/surface.py`` conformance-checks it (surface.py:73/85). Naming
# ``dm_available`` here is what makes the ``dm``→``dm_available`` drift a build failure: a
# consumer reading ``dm`` would leave ``dm_available`` produced-but-unconsumed.
register_field_consumer("ChannelReport", "dm_available")
