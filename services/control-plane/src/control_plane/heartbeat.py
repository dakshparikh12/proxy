"""Auxiliary observability — the Healthchecks.io dead-man ping (NOT the fence).

This ping is **auxiliary observability, not the liveness authority** (decision
D-003). The CANONICAL liveness fence is the ``operation_runs`` fencing heartbeat
in ``libs/ops/operation_run.py`` (spec §3.7 / CANONICAL §12.10): that heartbeat's
``UPDATE ... WHERE status='running'`` is what proves ownership — a zero-rowcount
beat drives ``is_owner`` False and every side-effect emit is gated on that flag
(``harness.emit``). Crash detection is a staleness read of that same row (the
boot bulk sweep + lazy per-read reaper), never a broker ack.

The Healthchecks.io ping is a SECONDARY, external alerting signal layered on top:
each meeting-harness process pings a check URL on an interval so a human/monitor
is paged when the process stops pinging (the process cannot report its own death,
so an outside dead-man switch does). It NEVER decides ownership, NEVER fences an
emit, and is NEVER consulted as the liveness authority — treating it as the fence
would be the split-brain D-003 forbids. The ping seam is injectable so the emit
path is testable without a live network call.
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

    This is the AUXILIARY observability ping (D-003) — an external dead-man alert,
    NOT the liveness fence. Ownership/liveness is decided solely by the
    ``operation_runs`` fencing heartbeat (``libs/ops/operation_run.py``); this
    function never touches that row and its result never gates an emit. ``ping``
    is injectable (defaults to a real HTTP GET) so it is testable without network.
    """
    do_ping = ping if ping is not None else _default_ping
    resp = do_ping(check_url)
    status = getattr(resp, "status_code", getattr(resp, "status", 200))
    return int(status) == 200
