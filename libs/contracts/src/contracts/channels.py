"""Channel report signal (AC-CMP-008). Field is named ``dm_available`` (bool)."""
from __future__ import annotations

from pydantic import BaseModel


class ChannelReport(BaseModel):
    dm_available: bool


# ── the LIVE channel-report consumer read is DERIVED, not hand-listed (§4.8 field-diff) ──
# ``services/transport/chat.py`` gates DM delivery on ``report.dm_available`` (chat.py:224)
# and ``services/transport/surface.py`` conformance-checks it (surface.py:73/85, via an
# ``isinstance(sig, ChannelReport)`` narrow). Those attribute reads are DERIVED by the
# ``contracts.contract_reads`` AST sweep, so a ``dm``→``dm_available`` drift (a consumer
# reading ``report.dm``) shows up as a real consumed-but-never-produced orphan on the real path.
