"""tenant_id reachability — add the tenant boundary to webhook_events

Revision ID: 0003_tenant_id_everywhere
Revises: 0002_note_deltas
Create Date: 2026-07-17

AC-TEN-001 (the tenant-isolation invariant, A-009): every durable, tenant-scoped
table must REACH ``tenants`` — either by carrying ``tenant_id`` as a declared FK,
or transitively through a declared ``meeting_id REFERENCES meetings(id)`` chain.

Post-``0002_note_deltas`` the only durable app tables that reached no tenant
boundary were ``operation_runs`` and ``webhook_events``:

  * ``webhook_events`` had neither a ``tenant_id`` column nor a ``meeting_id`` FK.
    This migration adds ``tenant_id uuid REFERENCES tenants(id)`` so the
    external-callback durability row is tenant-reachable. (The dedupe path keys
    on ``delivery_guid`` and is unaffected; the column is nullable.)

  * ``operation_runs`` is the ops table keyed by ``scope_id`` (text) — like
    ``sessions`` it is a coordination store, not a tenant-owned entity. Its
    canonical column set is pinned by the substrate contract (AC-SUB-001) to
    exactly {id, scope_id, operation_type, status, progress, result_ref, error,
    pause_requested, created_by, started_at, completed_at, last_heartbeat_at},
    with ``scope_id`` a NOT NULL ``text`` (not a uuid FK). It therefore cannot be
    given a ``tenant_id`` column (that would break the pinned contract) nor a
    declared FK on any existing column (``scope_id`` is text, not a uuid handle to
    a tenant-reaching table). Scoping ``operation_runs`` is left to the contract
    owner; this migration does not touch it.
"""
from __future__ import annotations

from alembic import op

revision = "0003_tenant_id_everywhere"
down_revision = "0002_note_deltas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE webhook_events "
        "ADD COLUMN IF NOT EXISTS tenant_id uuid REFERENCES tenants(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS tenant_id")
