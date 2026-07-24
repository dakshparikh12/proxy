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
    """Best-effort HTTPS GET to the Healthchecks.io check URL.

    The scheme is validated to be ``https`` BEFORE the URL is opened: urllib's
    opener otherwise honours ``file:``/``ftp:``/custom schemes (bandit B310,
    CWE-22), so a mis-supplied check URL could read a local file or reach an
    unexpected handler. Rejecting anything but https closes that gap at the seam.
    """
    import urllib.parse
    import urllib.request

    # https-only scheme asserted just above -> the urllib B310 audit is satisfied.
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(
            f"heartbeat ping URL must use the https scheme, got {url!r}"
        )
    return urllib.request.urlopen(url, timeout=5)  # noqa: S310  # nosec B310


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
