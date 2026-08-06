"""drop the dead scribe/chat tables — the workroom pivot left them writer/reader-less

Revision ID: 0011_drop_scribe_chat_tables
Revises: 0010_repos_tenant_fullname_uniq
Create Date: 2026-08-06

The reactive-workroom pivot removed the scribe/chat pipeline (§2.6 notes fold, the
cost-telemetry sink, the transcript status plane). After that removal these four durable
tables have NO live writer and NO reader anywhere in ``services``/``libs``:

  * ``transcript_segments``    — the §3.3 comprehension status plane (its repo was deleted);
  * ``meeting_cost``           — the persisted-spend upsert (its ``repos/cost.py`` was deleted);
  * ``meeting_cost_telemetry`` — the §11 per-micro-call sink (its ``ops/cost.py`` was deleted);
  * ``note_deltas``            — the append-only notes ledger; its SOLE remaining writer was the
                                 vestigial ``accept._apply_notes_edit`` (removed in this same
                                 change), and the §2.6 fold that read it is gone.

This is a forward migration (shipped 0001..0010 are never edited in place). It drops the four
tables; :func:`downgrade` recreates each at its exact head-0010 shape (the cumulative result of
their creating + altering migrations — note_deltas/transcript_segments at the 0004 §3.3 schema
with the 0007 defaults; meeting_cost from 0001; meeting_cost_telemetry from 0007) so the DAG
stays reversible. No kept table has an FK INTO any of these (they are leaf tables), so the drop
never orphans a live row; applying to PROD is human-gated like every revision.
"""
from __future__ import annotations

from alembic import op

revision = "0011_drop_scribe_chat_tables"
down_revision = "0010_repos_tenant_fullname_uniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Leaf tables — nothing references them; CASCADE is belt-and-suspenders for their own indexes.
    op.execute("DROP TABLE IF EXISTS note_deltas CASCADE")
    op.execute("DROP TABLE IF EXISTS transcript_segments CASCADE")
    op.execute("DROP TABLE IF EXISTS meeting_cost_telemetry CASCADE")
    op.execute("DROP TABLE IF EXISTS meeting_cost CASCADE")


def downgrade() -> None:
    # Recreate each table at its head-0010 shape (single source of truth = the raw DDL below,
    # mirroring the creating/altering migrations exactly).

    # note_deltas — 0004 §3.3 schema + 0007 seed-enabling defaults.
    op.execute(
        """
        CREATE TABLE note_deltas (
            id             bigserial PRIMARY KEY,
            meeting_id     uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id      uuid REFERENCES tenants(id),
            entry_id       text NOT NULL DEFAULT '',
            op             text NOT NULL DEFAULT 'add' CHECK (op IN ('add', 'patch', 'close')),
            payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
            window_start_s double precision,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX note_deltas_meeting_id_id_idx ON note_deltas (meeting_id, id)")
    op.execute(
        "CREATE UNIQUE INDEX note_deltas_source_window_uniq "
        "ON note_deltas (meeting_id, window_start_s, entry_id, op)"
    )

    # transcript_segments — 0004 §3.3 schema + 0007 seed-enabling defaults.
    op.execute(
        """
        CREATE TABLE transcript_segments (
            id          bigserial PRIMARY KEY,
            meeting_id  uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   uuid REFERENCES tenants(id),
            speaker     text,
            text        text NOT NULL DEFAULT '',
            start_s     double precision,
            end_s       double precision,
            status      text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'comprehended', 'gap')),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX transcript_segments_meeting_id_id_idx ON transcript_segments (meeting_id, id)"
    )

    # meeting_cost — 0001 substrate shape.
    op.execute(
        """
        CREATE TABLE meeting_cost (
            meeting_id uuid PRIMARY KEY REFERENCES meetings(id),
            model_usd double precision NOT NULL DEFAULT 0,
            cache_read_usd double precision NOT NULL DEFAULT 0,
            cache_creation_usd double precision NOT NULL DEFAULT 0,
            transport_usd double precision NOT NULL DEFAULT 0,
            e2b_usd double precision NOT NULL DEFAULT 0,
            started_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # meeting_cost_telemetry — 0007 shape (with its seed-enabling defaults).
    op.execute(
        """
        CREATE TABLE meeting_cost_telemetry (
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
