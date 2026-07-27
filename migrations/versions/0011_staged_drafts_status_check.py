"""staged_drafts.status — enforce CANONICAL §4's enum in the database

Revision ID: 0011_staged_drafts_status_check
Revises: 0010_clarify_items
Create Date: 2026-07-27

``staged_drafts.status`` has always been documented as a four-value enum —
``proposed | accepted | rejected | applied`` (CANONICAL §4 line 125) — but that enum
lived only in a SQL COMMENT. The column as shipped is:

    status text NOT NULL DEFAULT 'proposed'      -- 0001_substrate.py:141

with no CHECK. Any string was accepted. Compare ``operation_runs.status``
(0001_substrate.py:88), which DOES carry ``CHECK (status IN (...))`` — so this was an
inconsistency inside a single migration, not a deliberate policy.

This matters more than a normal schema tidy-up because of the defect P8 fixed. Doc 05
told builders, in three separate places, that ``propose_change`` returns the draft with
``status=needs_review`` — a value outside the enum, produced by confusing the draft-row
status with the *envelope* status (where ``needs_review`` IS valid, CANONICAL §1.2). The
prose is fixed (P8, P8b), but nothing stopped that value from having been written, and
nothing stops the next such value. AC-PME-15 and AC-PME-15-NEG both assert the database
itself rejects an out-of-enum status.

0001 is shipped and is NEVER edited in place; this is a FORWARD reconciliation migration,
the same discipline as 0005_webhook_events_canonical and 0008_substrate_schema_gaps.

Existing rows are normalised before the constraint is added. ``needs_review`` is mapped to
``proposed``, which is its correct value: Doc 04 §3.16.1 already reads a freshly staged
draft as ``status='proposed'``, so a row written as ``needs_review`` was a
never-yet-accepted draft — exactly what ``proposed`` means. This mapping is narrow and
deliberate. Any OTHER out-of-enum value is left alone and will make ``ADD CONSTRAINT``
fail loudly: an unknown status is a real defect that deserves a human, not a silent
coercion into the nearest enum member.
"""

from alembic import op

revision = "0011_staged_drafts_status_check"
down_revision = "0010_clarify_items"
branch_labels = None
depends_on = None

_ENUM = "('proposed', 'accepted', 'rejected', 'applied')"


def upgrade() -> None:
    # The one narrow, justified normalisation (see docstring).
    op.execute(
        "UPDATE staged_drafts SET status = 'proposed' WHERE status = 'needs_review'"
    )

    # Any surviving out-of-enum value fails the next statement on purpose.
    op.execute(
        f"""
        ALTER TABLE staged_drafts
            ADD CONSTRAINT staged_drafts_status_enum
            CHECK (status IN {_ENUM})
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE staged_drafts DROP CONSTRAINT IF EXISTS staged_drafts_status_enum"
    )
