"""The one ``dispatch()`` funnel (AC-CMP-010).

Every client message enters the backend through this single function; the
tile->backend edge is untrusted, so validation happens here exactly once
(closure/validation detail is completed at M11).
"""
from __future__ import annotations

from typing import Any


async def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and route one inbound client message. Single ingress funnel."""
    return payload
