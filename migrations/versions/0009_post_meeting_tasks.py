"""post_meeting_tasks — Doc 07 §4 product lifecycle state (NOT a second run table)

Revision ID: 0009_post_meeting_tasks
Revises: 0008_substrate_schema_gaps
Create Date: 2026-07-27

Doc 07 §4's one new durable store. Columns are Doc 07 §4's list, plus ``tenant_id``
per Invariant 9 (tenant reachability) and ``meeting_id uuid`` per CANONICAL §11.2.

**This is NOT the forbidden ``workroom_tasks`` table** (CANONICAL §12.11 rejects it, and
§13.2 D07.1 restates the prohibition). The distinction is load-bearing and the schema
enforces it: this table carries NO ``status``/``progress``/``last_heartbeat_at`` column.
The RUN's record stays the ``operation_runs`` row, unduplicated. What lives here is the
product lifecycle that exists *before* any run (tier, owner, plan, approval) and *after*
it (outcome) — and most rows (informational, question, ticket) never spawn a run at all.
``operation_ref`` is a pointer to that one row, never a copy of its state.

Per amendment P10 (ruling on contradiction C-A) the run this points at is keyed
``scope_id`` = meeting_id and ``operation_type`` = ``workroom:{task_id}`` —
see services/harness/src/harness/dispatch.py:129-145.

TWO DATABASE-LEVEL GUARDS (Doc 07 §3.9 invariants, enforced in the substrate rather than
only in tests — constraints hold when the harness lies):

  1. CHECK ``state <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)``
     — APPROVED is written only by a named human's action (D07.2). A row claiming APPROVED
     without both approver fields is rejected at write time.

  2. BEFORE INSERT OR UPDATE trigger — RUNNING is entered only from APPROVED (§3.9).

Two deliberate details in guard 2, both of which matter:

  * The UPDATE arm fires only on a genuine *transition into* RUNNING
    (``OLD.state IS DISTINCT FROM 'RUNNING'``). Without that clause every ordinary update
    to an already-RUNNING row — a cost write, an outcome write — would compare
    ``OLD.state='RUNNING' <> 'APPROVED'`` and raise. The invariant is about the
    transition, not about touching a running row.

  * The INSERT arm is an ADDITION beyond the letter of the build brief, which specified
    only a BEFORE UPDATE trigger. A BEFORE UPDATE trigger alone is trivially bypassed by
    ``INSERT ... state='RUNNING'``, which enters RUNNING without ever being APPROVED —
    the exact thing the invariant forbids. AC-PME-07-NEG asserts the database rejects
    this "independently of application code", so the guard has to cover both write paths
    or it is not a guard. Flagged here rather than applied silently.

Naming note: Doc 07 §4 lists the column as ``cost``. It is created here as ``cost_usd``
to match the established repo convention of naming the unit in the column
(``meeting_cost`` carries five ``*_usd`` columns, 0001_substrate.py:118-127). A unitless
money column is the kind of ambiguity GENERATOR.md §4.4 exists to catch. Flagged as a
deliberate deviation from the spec's column list.
"""

from alembic import op

revision = "0009_post_meeting_tasks"
down_revision = "0008_substrate_schema_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE post_meeting_tasks (
            task_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES tenants(id),
            meeting_id    uuid NOT NULL REFERENCES meetings(id),

            -- where the item came from (Doc 07 §3.1 intake)
            source        text NOT NULL
                          CHECK (source IN ('close-item', 'doc06-work')),
            item_ref      text NOT NULL,

            -- triage output (Doc 07 §3.1). NULL until TRIAGED.
            tier          text
                          CHECK (tier IS NULL OR tier IN (
                              'informational', 'question', 'ticket',
                              'ticket+plan', 'ticket+plan+draft')),

            -- Doc 07 §3.2: an owner comes from the room or the item is UNRESOLVED.
            -- A real value, NOT NULL, deliberately distinct from empty string.
            owner         text NOT NULL DEFAULT 'UNRESOLVED'
                          CHECK (owner <> ''),

            -- Doc 07 §3.9 state machine.
            state         text NOT NULL DEFAULT 'EXTRACTED'
                          CHECK (state IN (
                              'EXTRACTED', 'TRIAGED', 'CLARIFYING', 'PLANNED',
                              'APPROVED', 'RUNNING', 'DRAFTED',
                              'ACCEPTED', 'CHANGES_REQUESTED', 'DISCARDED')),

            -- the plan text lives on this record (Doc 07 §3.4)
            plan          text,

            -- approval identity (Doc 07 §3.4 / D07.2)
            approved_by   text,
            approved_at   timestamptz,

            -- pointer to the ONE run row; never a copy of its state
            operation_ref uuid REFERENCES operation_runs(id),
            -- the staged artifact, once one exists (Doc 07 §3.7)
            draft_id      uuid REFERENCES staged_drafts(draft_id),

            cost_usd      double precision NOT NULL DEFAULT 0,
            outcome       text,

            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),

            -- GUARD 1: APPROVED requires a named human and a timestamp (D07.2).
            CONSTRAINT post_meeting_tasks_approved_needs_approver
                CHECK (state <> 'APPROVED'
                       OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
        )
        """
    )

    # Per-meeting sweep (intake, caps) and per-tenant sweep (isolation reachability,
    # max_concurrent_tasks). Both are read on the dispatch path.
    op.execute(
        "CREATE INDEX post_meeting_tasks_meeting_idx ON post_meeting_tasks (meeting_id)"
    )
    op.execute(
        "CREATE INDEX post_meeting_tasks_tenant_state_idx "
        "ON post_meeting_tasks (tenant_id, state)"
    )

    # GUARD 2: RUNNING is entered only from APPROVED (Doc 07 §3.9).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION post_meeting_tasks_running_requires_approved()
        RETURNS trigger AS $fn$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                -- A row cannot be born RUNNING: that would enter RUNNING without ever
                -- having been APPROVED, bypassing the gate entirely.
                IF NEW.state = 'RUNNING' THEN
                    RAISE EXCEPTION
                        'post_meeting_tasks: cannot INSERT a row directly at RUNNING; '
                        'RUNNING is entered only from APPROVED (Doc 07 3.9)';
                END IF;
            ELSE
                -- Only a genuine transition INTO running is gated. Ordinary updates to a
                -- row that is already RUNNING (cost, outcome) must pass untouched.
                IF NEW.state = 'RUNNING'
                   AND OLD.state IS DISTINCT FROM 'RUNNING'
                   AND OLD.state <> 'APPROVED' THEN
                    RAISE EXCEPTION
                        'post_meeting_tasks: RUNNING may only be entered from APPROVED '
                        '(was %) (Doc 07 3.9)', OLD.state;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER post_meeting_tasks_running_gate
            BEFORE INSERT OR UPDATE ON post_meeting_tasks
            FOR EACH ROW
            EXECUTE FUNCTION post_meeting_tasks_running_requires_approved()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS post_meeting_tasks_running_gate ON post_meeting_tasks"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS post_meeting_tasks_running_requires_approved()"
    )
    op.execute("DROP TABLE IF EXISTS post_meeting_tasks CASCADE")
