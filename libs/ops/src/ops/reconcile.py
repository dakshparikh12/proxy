"""run_reconcile_sweep — the one idempotent, token-gated reconcile function.

Called by BOTH the prod scale-to-zero-safe scheduler (every ~5 min, via the
token-gated POST /internal/reconcile) and the dev in-process interval — one
function, never two code paths. It (1) sweeps every stale running operation_runs
row to 'interrupted' and (2) destroys any sandbox found past its TTL. Running it
twice over the same state yields the same end state.

Dual-path (mirrors ``libs.ops.cost``): the first argument's type selects the
path.

  * a :class:`~libs.db.Database` → the async persisted sweep (returns a coroutine
    the harness boundary awaits);
  * a raw psycopg connection → the synchronous sweep. Two synchronous shapes
    share this seam:
      - ``run_reconcile_sweep(conn, token=...)`` the token-gated /internal
        reconcile: an invalid/absent token is refused; the returned end state is
        idempotent (the same value on a second run over the same state);
      - ``run_reconcile_sweep(conn=conn, tenant=..., gcs=..., reason=...)`` the
        tenant-offboarding sweep: deletes the offboarded tenant's tenant-scoped
        Postgres rows and its GCS prefixes.
"""
from __future__ import annotations

import os
from typing import Any

from libs.db import Database, sandbox_ttl_s, stale_after_s

from .sandbox_provider import destroy

# The dev/test default for the /internal/reconcile token; production binds the
# real value via the ``INTERNAL_RECONCILE_TOKEN`` secret (Secret Manager).
_DEV_INTERNAL_TOKEN = "internal-secret"  # nosec B105 - dev default, prod uses the secret

_UNSET: Any = object()


async def _sandboxes_past_ttl(db: Database, ttl_s: int) -> list[Any]:
    """Sandboxes whose age exceeds the TTL (E2B list; empty for the MVP stub)."""
    # No sandbox registry table exists (no warm pool, no FSM); the reconcile
    # cron reconciles against the E2B API's live-sandbox list. For the local
    # substrate build this returns no leaked sandboxes.
    _ = (db, ttl_s)
    return []


async def _run_reconcile_sweep_async(db: Database) -> int:
    """Idempotently reap stale runs and destroy any TTL-expired sandbox."""
    swept = await db.sweep_stale_operation_runs()

    ttl = sandbox_ttl_s()
    for handle in await _sandboxes_past_ttl(db, ttl):
        await destroy(handle)  # kill any sandbox past its TTL

    return swept


def _valid_internal_token(token: Any) -> bool:
    """True iff ``token`` matches the bound internal-reconcile token."""
    expected = os.environ.get("INTERNAL_RECONCILE_TOKEN") or _DEV_INTERNAL_TOKEN
    return bool(token) and token == expected


def _reconcile_sweep_sync(conn: Any) -> int:
    """Flip every stale running row to 'interrupted'; return an idempotent state.

    The end state is the count of rows still 'running' after the sweep — stable
    across repeated runs over the same substrate (the second run finds nothing
    stale left to flip), so two consecutive calls return the same value.
    """
    conn.execute(
        "UPDATE operation_runs SET status = 'interrupted' "
        "WHERE status = 'running' "
        "AND last_heartbeat_at < now() - make_interval(secs => %s)",
        (float(stale_after_s()),),
    )
    cur = conn.execute(
        "SELECT COUNT(*) FROM operation_runs WHERE status = 'running'"
    )
    return int(cur.fetchone()[0])


def _tenant_scoped_columns(conn: Any) -> list[tuple[str, str]]:
    """(table, column) for every public table carrying a tenant/tenant_id column."""
    cur = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE column_name IN ('tenant', 'tenant_id') AND table_schema = 'public'"
    )
    return [(row[0], row[1]) for row in cur.fetchall()]


def _offboard_sweep_sync(
    conn: Any, *, tenant: str, gcs: Any = None, reason: str | None = None
) -> dict[str, Any]:
    """Delete an offboarded tenant's Postgres rows + GCS prefixes (idempotent)."""
    from psycopg import sql  # identifier-safe SQL composition (no string interpolation)

    deleted = 0
    for table, column in _tenant_scoped_columns(conn):
        # Compare as text so a text tenant id never mis-casts against a uuid column
        # (matching nothing is correct; it must never raise). Table/column come
        # from the information_schema catalog and are quoted via sql.Identifier,
        # so the statement is fully parameterized (no interpolated SQL text).
        query = sql.SQL("DELETE FROM {} WHERE {}::text = %s").format(
            sql.Identifier(table), sql.Identifier(column)
        )
        cur = conn.execute(query, (str(tenant),))
        deleted += int(getattr(cur, "rowcount", 0) or 0)

    if gcs is not None:
        # The tenant owns a GCS prefix namespace; drop every object under it.
        gcs.delete_prefix(f"tenants/{tenant}/")

    return {"tenant": str(tenant), "reason": reason, "rows_deleted": deleted}


def run_reconcile_sweep(
    target: Any = None,
    *,
    conn: Any = None,
    token: Any = _UNSET,
    tenant: str | None = None,
    gcs: Any = None,
    reason: str | None = None,
) -> Any:
    """Reconcile: async persisted sweep, sync token-gated sweep, or offboard sweep."""
    handle = target if target is not None else conn
    if isinstance(handle, Database):
        return _run_reconcile_sweep_async(handle)

    if tenant is not None:
        return _offboard_sweep_sync(handle, tenant=tenant, gcs=gcs, reason=reason)

    provided = None if token is _UNSET else token
    if not _valid_internal_token(provided):
        raise PermissionError(
            "run_reconcile_sweep requires a valid internal-reconcile token"
        )
    return _reconcile_sweep_sync(handle)
