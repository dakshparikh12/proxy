"""doc03 note-store — reconcile note_deltas to the sealed §3.3 schema + add transcript_segments

Revision ID: 0004_doc03_note_store_v33
Revises: 0003_tenant_id_everywhere
Create Date: 2026-07-21

0002_note_deltas created an early note_deltas (uuid id + a single ``body text``
column) that PREDATES the sealed 03-MEETING-UNDERSTANDING §3.3 schema. The sealed
criteria + the NotesRepository SQL + the doc03 db-tier tests all require the §3.3
shape: append-only ``note_deltas`` (bigserial id, entry_id, op, payload jsonb,
window_start_s) with the replay-idempotency UNIQUE INDEX, and the ``transcript_segments``
append/status plane (which no prior migration created). This revision drops the
early table and recreates both planes at the §3.3 schema. ``tenant_id`` is kept as a
NULLABLE column so the table stays tenant-reachable (invariant 9) without breaking the
§3.3 inserts (which do not name it). meeting_id references meetings(id) (substrate, 0001).
"""
from __future__ import annotations

from alembic import op

revision = "0004_doc03_note_store_v33"
down_revision = "0003_tenant_id_everywhere"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace the pre-§3.3 note_deltas with the sealed schema.
    op.execute("DROP TABLE IF EXISTS note_deltas CASCADE")
    op.execute(
        """
        CREATE TABLE note_deltas (
            id             bigserial PRIMARY KEY,
            meeting_id     uuid NOT NULL,   -- §3.3: no FK (append-only, standalone)
            tenant_id      uuid REFERENCES tenants(id),  -- nullable declared FK (AC-TEN-001)
            entry_id       text NOT NULL,
            op             text NOT NULL CHECK (op IN ('add', 'patch', 'close')),
            payload        jsonb NOT NULL,
            window_start_s double precision,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX note_deltas_meeting_id_id_idx ON note_deltas (meeting_id, id)")
    # Replay idempotency (§3.3 / CANONICAL §12.10): a stray re-append is a silent no-op.
    op.execute(
        "CREATE UNIQUE INDEX note_deltas_source_window_uniq "
        "ON note_deltas (meeting_id, window_start_s, entry_id, op)"
    )

    # transcript_segments — the append/status plane (§3.3), not created by any prior migration.
    op.execute("DROP TABLE IF EXISTS transcript_segments CASCADE")
    op.execute(
        """
        CREATE TABLE transcript_segments (
            id          bigserial PRIMARY KEY,
            meeting_id  uuid NOT NULL,                           -- §3.3 exact: no FK
            tenant_id   uuid REFERENCES tenants(id),
            speaker     text,
            text        text NOT NULL,
            start_s     double precision,
            end_s       double precision,
            status      text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'comprehended', 'gap')),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX transcript_segments_meeting_id_id_idx ON transcript_segments (meeting_id, id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transcript_segments CASCADE")
    op.execute("DROP TABLE IF EXISTS note_deltas CASCADE")
    # Restore the 0002-era note_deltas so downgrade returns to the prior head's shape.
    op.execute(
        """
        CREATE TABLE note_deltas (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id uuid NOT NULL REFERENCES meetings(id),
            tenant_id uuid REFERENCES tenants(id),
            op text NOT NULL DEFAULT 'add' CHECK (op IN ('add', 'patch', 'close')),
            body text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX note_deltas_meeting_idx ON note_deltas (meeting_id)")
