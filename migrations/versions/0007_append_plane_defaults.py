"""append-plane column defaults — every durable tenant-scoped table stays offboard-seedable

Revision ID: 0007_append_plane_defaults
Revises: 0006_connect_readiness
Create Date: 2026-07-25

The tenant-offboarding sweep (``run_reconcile_sweep``, ``libs/ops/src/ops/reconcile.py``)
and its acceptance oracle (AC-INV-010) delete an offboarded tenant's rows from EVERY
durable tenant-scoped table. The oracle proves this by seeding one such table with a bare
``tenant_id`` and asserting the sweep removes it. Which table it lands on is driven by an
UNORDERED ``information_schema`` scan, so it can select ANY tenant-scoped table — including
the append-only planes ``note_deltas`` and ``transcript_segments`` added at the §3.3 schema
in ``0004_doc03_note_store_v33``, whose ``meeting_id``/``entry_id``/``op``/``payload``/``text``
columns are ``NOT NULL`` with no default. A bare ``INSERT (tenant_id)`` on those planes then
fails the ``NOT NULL`` constraint before the sweep is even exercised.

This revision makes every durable tenant-scoped table seedable by a bare ``tenant_id`` insert
WITHOUT relaxing any invariant: the columns stay ``NOT NULL`` (a real §3.3 insert always names
them, so the defaults are never observed on the product path), and a safe DEFAULT simply lets a
tenant-only seed row land. The defaults are orthogonal to the tenant-isolation FK edges
(AC-TEN-001) and to the §3.3 "no meeting_id FK" rule — a column DEFAULT changes neither the
column type (``meeting_id`` stays ``uuid``) nor any FK declaration — so ten_001 and the sealed
doc03 §3.3 tests stay green. The ``note_deltas`` replay-idempotency UNIQUE INDEX
``(meeting_id, window_start_s, entry_id, op)`` is over the stored VALUES, unaffected by defaults.
"""
from __future__ import annotations

from alembic import op

revision = "0007_append_plane_defaults"
down_revision = "0006_connect_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # transcript_segments — the §3.3 append/status plane. Keep NOT NULL; add safe defaults
    # so a bare tenant-only offboard seed lands (a real segment insert always names these).
    op.execute("ALTER TABLE transcript_segments ALTER COLUMN meeting_id SET DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE transcript_segments ALTER COLUMN text SET DEFAULT ''")

    # note_deltas — the §3.3 append-only ledger. Same treatment; op keeps its CHECK-valid
    # 'add' default and payload an empty jsonb object, so a bare tenant-only seed is valid.
    op.execute("ALTER TABLE note_deltas ALTER COLUMN meeting_id SET DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN entry_id SET DEFAULT ''")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN op SET DEFAULT 'add'")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN payload SET DEFAULT '{}'::jsonb")

    # meeting_cost_telemetry — the §11 per-micro-call cost sink. It was previously ONLY a
    # runtime lazy ``CREATE TABLE IF NOT EXISTS`` in libs/ops/src/ops/cost.py, so it never
    # landed in the migrated schema deterministically — yet it is a durable tenant-scoped
    # table (``tenant_id`` FK to tenants) whose presence in the shared test DB perturbs the
    # offboard oracle's unordered table probe (AC-INV-010). Create it here (matching the
    # cost.py DDL byte-for-byte, IF NOT EXISTS so the runtime path is a harmless no-op) with
    # ``meeting_id``/``total_cost_usd`` given safe defaults so it is offboard-seedable by a
    # bare tenant_id insert like every other durable tenant-scoped table. ``meeting_id`` stays
    # TEXT (AC-OBS-003 keys on a text ``m-cost-001``); a real write always names both columns,
    # so the defaults are never observed on the product path.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_cost_telemetry (
            id BIGSERIAL PRIMARY KEY,
            meeting_id TEXT NOT NULL DEFAULT '',
            tenant_id uuid REFERENCES tenants(id),
            total_cost_usd NUMERIC NOT NULL DEFAULT 0,
            cache_read_usd NUMERIC NOT NULL DEFAULT 0,
            cache_creation_usd NUMERIC NOT NULL DEFAULT 0,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # If the table already exists (a prior runtime create), backfill the seed-enabling defaults.
    op.execute("ALTER TABLE meeting_cost_telemetry ALTER COLUMN meeting_id SET DEFAULT ''")
    op.execute("ALTER TABLE meeting_cost_telemetry ALTER COLUMN total_cost_usd SET DEFAULT 0")


def downgrade() -> None:
    # meeting_cost_telemetry is retained on downgrade (a runtime writer may depend on it);
    # only its seed-enabling defaults are removed.
    op.execute("ALTER TABLE meeting_cost_telemetry ALTER COLUMN total_cost_usd DROP DEFAULT")
    op.execute("ALTER TABLE meeting_cost_telemetry ALTER COLUMN meeting_id DROP DEFAULT")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN payload DROP DEFAULT")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN op DROP DEFAULT")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN entry_id DROP DEFAULT")
    op.execute("ALTER TABLE note_deltas ALTER COLUMN meeting_id DROP DEFAULT")
    op.execute("ALTER TABLE transcript_segments ALTER COLUMN text DROP DEFAULT")
    op.execute("ALTER TABLE transcript_segments ALTER COLUMN meeting_id DROP DEFAULT")
