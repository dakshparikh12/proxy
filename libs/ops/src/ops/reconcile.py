"""run_reconcile_sweep — the one idempotent, token-gated reconcile function.

Called by BOTH the prod scale-to-zero-safe scheduler (every ~5 min, via the
token-gated POST /internal/reconcile) and the dev in-process interval — one
function, never two code paths (§3.8). The async persisted sweep runs THREE
ISOLATED steps, each in its OWN try/except so one bad step never aborts the rest:
(1) ``stale-harnesses`` — reap orphaned operation_runs (the redelivered-join
dedup reaper); (2) ``meeting-sandboxes`` — list live sandboxes and destroy any
orphaned/past-TTL (§3.9); (3) ``notes-retention`` — the retention hook. Every
step is idempotent, so running the sweep twice over the same state yields the
same end state.

Dual-path: the first argument's type selects the
path.

  * a :class:`~libs.db.Database` → the async persisted sweep (returns a coroutine
    the harness boundary awaits, resolving to the ``{"steps", "errors"}`` report);
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

import hmac
import os
from typing import Any

from libs.db import Database, stale_after_s

from . import sandbox_provider

# The dev/test default for the /internal/reconcile token; production binds the
# real value via the ``INTERNAL_RECONCILE_TOKEN`` secret (Secret Manager).
_DEV_INTERNAL_TOKEN = "internal-secret"  # nosec B105 - dev default, prod uses the secret

_UNSET: Any = object()


async def _step_stale_harnesses(db: Database) -> None:
    """Reap orphaned operation_runs (the §3.7 reaper — redelivered-join dedup).

    Flips every stale 'running' row to 'interrupted' so a redelivered join can
    re-claim the meeting rather than double-run it. Idempotent — a second pass
    over the same state finds nothing stale left to flip.
    """
    await db.sweep_stale_operation_runs()


async def _step_meeting_sandboxes(db: Database) -> None:
    """Reap orphaned/past-TTL sandboxes (§3.9 defence #3 — list live, kill orphans).

    Cross-checks the live-sandbox list against ended meetings and the TTL; destroy
    tolerates a 404. Idempotent — a second pass over the reaped state is a no-op.
    """
    _ = db  # the live-sandbox view is the provider's (E2B list in prod), not the DB
    await sandbox_provider.reconcile_sandboxes()


async def _step_notes_retention(db: Database) -> None:
    """Notes-retention reconcile step (§3.8 third step).

    The core V0 notes plane is the append-only ``note_deltas`` ledger (the durable
    source of truth, §11.4); there is no separate retention sweep to run in V0 (the
    Tier-2 session mirror + its retention sweep were CUT per §6). This step exists
    as an isolated, idempotent no-op so the sweep's three-step shape and its
    per-step error isolation hold — a future retention policy slots in here without
    changing the sweep contract.
    """
    _ = db


# The three isolated reconcile steps, in order (§3.8). Each runs in its OWN
# try/except at the call site so one bad step never aborts the rest.
_RECONCILE_STEPS: tuple[tuple[str, Any], ...] = (
    ("stale-harnesses", _step_stale_harnesses),
    ("meeting-sandboxes", _step_meeting_sandboxes),
    ("notes-retention", _step_notes_retention),
)


async def _run_reconcile_sweep_async(db: Any) -> dict[str, Any]:
    """Run the three isolated reconcile steps idempotently (§3.8).

    Each step runs in its own try/except — a failure in one is captured and
    reported by name, never aborting the others. Returns the §3.8 report:
    ``{"steps": [...], "errors": [...]}``. Running the sweep twice over the same
    state yields the same end state (every step is idempotent).
    """
    errors: list[str] = []
    for name, step in _RECONCILE_STEPS:
        try:
            await step(db)
        except Exception as exc:  # noqa: BLE001 - per-step isolation is the contract
            errors.append(f"{name}: {exc}")
    return {"steps": [name for name, _ in _RECONCILE_STEPS], "errors": errors}


def _valid_internal_token(token: Any) -> bool:
    """True iff ``token`` matches the bound internal-reconcile token (constant-time).

    The compare is ``hmac.compare_digest`` (B4) — a naked ``==`` leaks the token's
    length/shared-prefix by timing, letting an attacker recover it byte-by-byte. In
    prod ``INTERNAL_RECONCILE_TOKEN`` is a boot hard-gate (settings), so the dev
    literal is a local-only fallback and never reaches a running production process.
    """
    expected = os.environ.get("INTERNAL_RECONCILE_TOKEN") or _DEV_INTERNAL_TOKEN
    if not token or not isinstance(token, str):
        return False
    return hmac.compare_digest(token, expected)


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
