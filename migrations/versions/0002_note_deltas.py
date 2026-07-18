"""note_deltas — the persisted, human-approved note-delta surface

Revision ID: 0002_note_deltas
Revises: 0001_substrate
Create Date: 2026-07-17

A revision-DAG child of the substrate migration. ``note_deltas`` carries the
per-meeting note deltas Scribe emits; ``meeting_id`` is ``uuid`` (canonical
§11.2, like every other app-table meeting handle) and ``tenant_id`` is present
so the table is tenant-reachable (A-009 / invariant 9).
"""
from __future__ import annotations

from alembic import op

revision = "0002_note_deltas"
down_revision = "0001_substrate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE note_deltas (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id uuid NOT NULL REFERENCES meetings(id),
            tenant_id uuid REFERENCES tenants(id),
            op text NOT NULL DEFAULT 'add'
                CHECK (op IN ('add', 'patch', 'close')),
            body text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX note_deltas_meeting_idx ON note_deltas (meeting_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_deltas CASCADE")
