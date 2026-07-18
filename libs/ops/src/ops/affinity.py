"""WebSocket / retry affinity — route a request to the owning instance (AC-OBS-007).

The owner instance-id is persisted on the claimed ``operation_runs`` row
(``created_by``). A tile-WS reconnect or a retried webhook that lands on a
non-owner instance must be proxied/handed off to the claim owner — never served
locally and never left to a random Cloud Run load-balancer pick. The owner
serves locally (affinity, not a blind proxy loop).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def route_to_owner(
    *,
    meeting_id: str,
    this_instance: str,
    lookup_owner: Callable[[str], str | None],
    proxy: Callable[[str, Any], Any],
    serve_local: Callable[[Any], Any],
    request: Any = None,
) -> None:
    """Route ``request`` for ``meeting_id`` to the operation_runs claim owner.

    If this instance is not the owner, hand the request off to the owner via
    ``proxy``; otherwise serve it locally. An unknown owner (no live claim) is
    served locally by the receiving instance rather than dropped.
    """
    owner = lookup_owner(meeting_id)
    if owner is not None and owner != this_instance:
        proxy(owner, request)
        return
    serve_local(request)
