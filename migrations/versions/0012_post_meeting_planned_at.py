"""post_meeting_tasks.planned_at — the expiry clock B4 reads but could not get

Revision ID: 0012_post_meeting_planned_at
Revises: 0011_staged_drafts_status_check
Create Date: 2026-07-29

``plan.expire_stale_plans`` decides expiry from ``row["planned_at"]`` and
``config.plan_expiry`` (Doc 07 §3.4: *"A plan nobody answers expires quietly after
``plan_expiry``"*). **The column did not exist**, and ``PostMeetingTaskStore`` never
selected it — so on the real substrate every row fell into the
``not isinstance(planned_at, datetime)`` skip branch and **nothing ever expired**. The unit
tests passed because they hand-built row dicts carrying the key.

This is the same defect class as the ``operation_ref`` foreign key: a fake supplying a
field the real store cannot. AC-PME-08 and AC-PME-08-NEG were unit-green and could not
have run against Postgres.

``planned_at timestamptz`` is NULLABLE and deliberately distinct from ``created_at``:

* ``created_at`` is when the item was extracted (B1) — it starts ticking while the task is
  still being triaged and clarified, so expiry measured from it would penalise a task for
  the time Proxy spent thinking rather than the time the human spent not answering.
* ``planned_at`` is when the plan was put in front of a human (B4). That is the moment
  §3.4's clock should start, and it is what makes the expiry window mean "unanswered for
  N hours" rather than "extracted N hours ago".

It is re-stamped when the owner **edits** a plan (§3.4): an edited plan is a new plan
awaiting a fresh decision, so the clock restarts rather than the owner inheriting the
remains of the previous window.

NULL means "no plan has been presented", which the sweep treats as not-expirable — the
same fail-safe direction as an unreadable state.
"""

from alembic import op

revision = "0012_post_meeting_planned_at"
down_revision = "0011_staged_drafts_status_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE post_meeting_tasks "
        "ADD COLUMN IF NOT EXISTS planned_at timestamptz"
    )
    # The expiry sweep reads (state, planned_at) for one tenant/meeting. Partial index on
    # the only state it ever sweeps, so the scan stays proportional to open plans rather
    # than to every task ever created.
    op.execute(
        "CREATE INDEX IF NOT EXISTS post_meeting_tasks_planned_sweep_idx "
        "ON post_meeting_tasks (planned_at) WHERE state = 'PLANNED'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS post_meeting_tasks_planned_sweep_idx")
    op.execute("ALTER TABLE post_meeting_tasks DROP COLUMN IF EXISTS planned_at")
