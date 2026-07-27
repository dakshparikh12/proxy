"""clarify_items — the clarifying-question record, CO-OWNED by Docs 06 and 07

Revision ID: 0010_clarify_items
Revises: 0009_post_meeting_tasks
Create Date: 2026-07-27

**OWNERSHIP (founder ruling on contradiction C-C, 2026-07-27) — read this before editing.**

This table is *defined* in Doc 06 §4 (Proactive), which is SPEC'D but NOT BUILT. Doc 06
§3.6 describes it as "written by Doc 06, read and completed by Doc 07"; Doc 07 §3.3 has
Doc 07 *creating* rows. Both could not be the whole picture, and while Doc 06 is unbuilt
the table had no builder at all.

Ruling: **Doc 07 creates and owns ``clarify_items`` until Doc 06 lands.** Hence this
migration, which ships with Doc 07. When Doc 06 is built it becomes a co-writer of the
same table — it does NOT create a second one, and it does NOT need a migration of its
own. The column list below is Doc 06 §4's, unchanged, precisely so that Doc 06 can adopt
it without a schema change:

    question, kind, blocking_ref, urgency, answer, answered_by

plus ``tenant_id`` per Invariant 9 (tenant reachability) and ``meeting_id uuid`` per
CANONICAL §11.2.

WHY THIS TABLE IS WRITABLE BEFORE APPROVAL. Doc 07 §3.4's invariant is that no durable
write occurs outside the task's own record before a named human approves the plan. This
table is the ONE carve-out (founder ruling on contradiction C-D, same date): §3.3 writes
a clarify item while the task is in CLARIFYING, a state strictly before APPROVED. The
carve-out is CLOSED — ``clarify_items`` is the only exempt table, because asking a
question is not a world-change. AC-PME-07 asserts exactly that: the permitted pre-approval
write set is {post_meeting_tasks, clarify_items} and a write to any third table fails.

``kind`` and ``urgency`` deliberately carry NO CHECK constraint: Doc 06 §4 names the
columns but enumerates neither vocabulary, and inventing an enum here would pin a domain
no authority has stated. Doc 06 owns those vocabularies and can add the constraints when
it lands.
"""

from alembic import op

revision = "0010_clarify_items"
down_revision = "0009_post_meeting_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE clarify_items (
            clarify_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL REFERENCES tenants(id),
            meeting_id   uuid NOT NULL REFERENCES meetings(id),

            -- Doc 06 §4's column list, verbatim.
            question     text NOT NULL,
            kind         text,
            blocking_ref text,
            urgency      text,
            answer       text,
            answered_by  text,

            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # The pending sweep: Doc 07 §3.3 holds an item while its question is unanswered, and
    # AC-PME-04 asserts an unroutable question stays pending and surfaces on the draft
    # card. Partial index because the answered rows are never swept.
    op.execute(
        "CREATE INDEX clarify_items_pending_idx "
        "ON clarify_items (meeting_id) WHERE answer IS NULL"
    )
    # Tenant-scoped sweep (isolation reachability).
    op.execute(
        "CREATE INDEX clarify_items_tenant_idx ON clarify_items (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS clarify_items CASCADE")
