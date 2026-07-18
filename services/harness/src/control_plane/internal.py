"""control_plane internal routes — the token-gated reconcile entrypoint.

POST /internal/reconcile is mounted OUTSIDE the auth wall and gated by the
internal token. It calls the SAME run_reconcile_sweep the dev in-process interval
calls (one function, two schedulers — prod 5-min cron + dev interval). Idempotent.
"""
from __future__ import annotations

import asyncio

from libs.db import Database
from libs.ops import run_reconcile_sweep

INTERNAL_RECONCILE_PATH = "/internal/reconcile"
INTERNAL_TOKEN_HEADER = "X-Internal-Token"  # nosec B105 - HTTP header name, not a secret


async def handle_internal_reconcile(
    db: Database, *, provided_token: str | None, expected_token: str | None
) -> int:
    """Token-gated POST /internal/reconcile handler; 403 without the token."""
    if not expected_token or provided_token != expected_token:
        return 403
    await run_reconcile_sweep(db)
    return 200


async def reconcile_interval_loop(db: Database, *, interval_s: float) -> None:
    """Dev in-process interval calling the same run_reconcile_sweep as prod."""
    while True:
        await run_reconcile_sweep(db)
        await asyncio.sleep(interval_s)
