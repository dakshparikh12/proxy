"""STT credential refresh — an availability-critical IN-PROCESS interval loop.

This runs on its own asyncio interval, NOT the scale-to-zero reconcile cron: an
availability-critical loop must not depend on a cron that only wakes on demand.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from libs.db import stt_refresh_interval_s


async def refresh_stt_credentials(
    refresh_fn: Callable[[], Awaitable[None]],
    *,
    interval_s: float | None = None,
) -> None:
    """Refresh STT (AssemblyAI) credentials forever on an in-process interval."""
    cadence = float(interval_s) if interval_s is not None else float(
        stt_refresh_interval_s()
    )
    while True:
        await refresh_fn()
        await asyncio.sleep(cadence)
