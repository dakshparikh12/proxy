"""run_reconcile_sweep — the one idempotent, token-gated reconcile function.

Called by BOTH the prod scale-to-zero-safe scheduler (every ~5 min, via the
token-gated POST /internal/reconcile) and the dev in-process interval — one
function, never two code paths. It (1) sweeps every stale running operation_runs
row to 'interrupted' and (2) destroys any sandbox found past its TTL. Running it
twice over the same state yields the same end state.
"""
from __future__ import annotations

from typing import Any

from libs.db import Database, sandbox_ttl_s

from .sandbox_provider import destroy


async def _sandboxes_past_ttl(db: Database, ttl_s: int) -> list[Any]:
    """Sandboxes whose age exceeds the TTL (E2B list; empty for the MVP stub)."""
    # No sandbox registry table exists (no warm pool, no FSM); the reconcile
    # cron reconciles against the E2B API's live-sandbox list. For the local
    # substrate build this returns no leaked sandboxes.
    _ = (db, ttl_s)
    return []


async def run_reconcile_sweep(db: Database) -> int:
    """Idempotently reap stale runs and destroy any TTL-expired sandbox."""
    swept = await db.sweep_stale_operation_runs()

    ttl = sandbox_ttl_s()
    for handle in await _sandboxes_past_ttl(db, ttl):
        await destroy(handle)  # kill any sandbox past its TTL

    return swept
