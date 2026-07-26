"""substrate schema gaps — repos.github_installation_id, meetings.platform, meetings.ended_at

Revision ID: 0008_substrate_schema_gaps
Revises: 0007_append_plane_defaults
Create Date: 2026-07-25

The frozen identity/tenancy schema (CANONICAL-DECISIONS.md §11.1 rows 213-217 /
00-FOUNDATION.md §5.7) mandates three columns that ``0001_substrate`` omitted:

    CREATE TABLE repos    (..., github_installation_id text, ...);   -- row 213-214
    CREATE TABLE meetings (..., platform text, ..., ended_at timestamptz);  -- row 215-217

0001 is shipped and is NEVER edited in place; this is a FORWARD reconciliation
migration (the same discipline as 0005_webhook_events_canonical). It adds the three
missing columns, handling existing rows so ``alembic upgrade head`` succeeds on the
migrated dev DB:

  * ``repos.github_installation_id text`` — the Nango GitHub-App grant link (records
    which installation granted repo access, CANONICAL §11.1 "the binding flow"
    step 2 / §5.7). NULLABLE: the connect flow may not carry it yet, but the column
    must exist per spec. ``ADD COLUMN`` of a nullable column is instant and needs no
    backfill.

  * ``meetings.platform text`` — the meeting platform (recall|zoom|teams|…), set at
    join where the meetings row is created (CANONICAL §11.1 meetings DDL). NULLABLE —
    existing meeting rows predate the column and legitimately have no platform, and a
    real join always names it going forward (the join path writes it).

  * ``meetings.ended_at timestamptz`` — the meeting-end timestamp, set on the ordered
    close when status→'ended' (CANONICAL §11.1 meetings DDL / §5.7). NULLABLE — a
    live/interrupted meeting has no end time, and only the ordered close stamps it.

All three are nullable (no server_default needed): adding a nullable column to a table
with existing rows is a metadata-only change that leaves the existing rows NULL, which
is the truthful value (an un-migrated repo has no recorded installation id; a live
meeting has not ended). This keeps the tenant-reachability invariant (AC-TEN-001) and
the meetings.status CHECK domain untouched — no FK edge or constraint changes, so
ten_001 and the sealed doc00 substrate tests stay green.

Idempotent via ``ADD COLUMN IF NOT EXISTS`` so a partially-applied dev DB re-migrates
cleanly.
"""
from __future__ import annotations

from alembic import op

revision = "0008_substrate_schema_gaps"
down_revision = "0007_append_plane_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # repos.github_installation_id — the Nango GitHub-App grant link (§11.1 rows 213-214).
    # Nullable: the connect→repo binding records it when the install callback resolves the
    # installation; existing rows legitimately have none.
    op.execute(
        "ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_installation_id text"
    )

    # meetings.platform — the meeting platform (recall|zoom|teams|…), set at join (§11.1 row 216).
    # Nullable: the join path writes it on new rows; existing rows predate the column.
    op.execute(
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS platform text"
    )

    # meetings.ended_at — the meeting-end timestamp, stamped on ordered-close (§11.1 row 217).
    # Nullable: a live/interrupted meeting has no end time; only the close sets it.
    op.execute(
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS ended_at timestamptz"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE meetings DROP COLUMN IF EXISTS ended_at")
    op.execute("ALTER TABLE meetings DROP COLUMN IF EXISTS platform")
    op.execute("ALTER TABLE repos DROP COLUMN IF EXISTS github_installation_id")
