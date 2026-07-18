"""The one ``dispatch()`` funnel (AC-CMP-010).

Every client message enters the backend through this single function; the
tile->backend edge is untrusted, so validation happens here exactly once
(closure/validation detail is completed at M11).

As a member of the emit/side-effect frontier (§5.1 + §12.3) it is gated on
``is_owner``: a process whose operation_runs row was reclaimed (is_owner False)
must not route a side-effecting message — the dispatch refuses and nothing lands.
"""
from __future__ import annotations

from typing import Any


async def dispatch(
    payload: dict[str, Any], *, is_owner: bool = True
) -> dict[str, Any]:
    """Validate and route one inbound client message. Single ingress funnel.

    Refuses (routes nothing) when the caller is not the meeting owner
    (``is_owner`` False) — the fence extends through the single dispatch funnel.
    """
    if not is_owner:
        return {"dispatched": False, "refused": True}
    return payload
