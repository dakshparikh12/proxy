"""Harness liveness — a Healthchecks.io dead-man heartbeat.

Each meeting-harness process pings a Healthchecks.io check URL on an interval; a
missed ping is what alerts (the harness cannot report its own death, so an
external dead-man switch does). The ping seam is injectable so the emit path is
testable without a live network call.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Healthchecks.io ping host (the check-specific slug is supplied per deployment).
HEALTHCHECKS_PING_HOST = "https://hc-ping.com"


def _default_ping(url: str) -> Any:
    """Best-effort HTTP GET to the Healthchecks.io check URL."""
    import urllib.request

    return urllib.request.urlopen(url, timeout=5)  # noqa: S310 - fixed https ping URL


def emit_heartbeat(
    *, check_url: str, ping: Callable[..., Any] | None = None
) -> bool:
    """Ping the Healthchecks.io check URL; return True on a successful ping.

    ``ping`` is injectable (defaults to a real HTTP GET) so the heartbeat can be
    driven deterministically in tests.
    """
    do_ping = ping if ping is not None else _default_ping
    resp = do_ping(check_url)
    status = getattr(resp, "status_code", getattr(resp, "status", 200))
    return int(status) == 200
