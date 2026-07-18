"""durable substrate — identity/tenancy + operation_runs + cost + drafts + webhooks

Revision ID: 0001_substrate
Revises:
Create Date: 2026-07-17

The one canonical Postgres schema for Doc 00 §5. Raw DDL (no ORM models) so the
column sets stay the single source of truth. operation_runs is the ONE durable
ops table (no meeting_harness / workroom_tasks / close_jobs / meeting_events /
feature_flags / meeting_cost_entries): workroom tasks and meeting-close are
operation_runs rows keyed by operation_type.
"""
from __future__ import annotations

from alembic import op

revision = "0001_substrate"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── identity / tenancy (A-009 tenant reachability) ──────────────────────
    op.execute(
        """
        CREATE TABLE tenants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid REFERENCES tenants(id),
            email text UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE repos (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid REFERENCES tenants(id),
            full_name text,
            default_branch text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE meetings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid REFERENCES tenants(id),
            repo_id uuid REFERENCES repos(id),
            status text NOT NULL DEFAULT 'live'
                CHECK (status IN ('live', 'ended', 'interrupted')),
            pinned_sha text,
            recall_bot_id text,
            meeting_url text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid REFERENCES users(id),
            tenant_id uuid REFERENCES tenants(id),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── operation_runs: the ONE durable ops table (heartbeat/claim/reconcile) ─
    op.execute(
        """
        CREATE TABLE operation_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            scope_id text NOT NULL,
            operation_type text NOT NULL,
            status text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
            progress jsonb,
            result_ref jsonb,
            error text,
            pause_requested boolean NOT NULL DEFAULT false,
            created_by text,
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            last_heartbeat_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Partial unique index: at most one running row per (scope_id, operation_type).
    # Non-running rows never block a re-claim; this is the atomic-claim target.
    op.execute(
        """
        CREATE UNIQUE INDEX operation_runs_one_running_per_scope
            ON operation_runs (scope_id, operation_type)
            WHERE status = 'running'
        """
    )
    op.execute(
        "CREATE INDEX operation_runs_stale_idx "
        "ON operation_runs (status, last_heartbeat_at)"
    )

    # ── meeting_cost: persisted spend (A-009 FK → meetings) ─────────────────
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

    # ── staged_drafts: durable at creation, survives Workroom teardown ──────
    op.execute(
        """
        CREATE TABLE staged_drafts (
            draft_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id uuid REFERENCES meetings(id),
            kind text,
            summary text,
            artifact_ref text,
            status text NOT NULL DEFAULT 'proposed',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── webhook_events: the ONLY external-callback durability (dedupe) ───────
    op.execute(
        """
        CREATE TABLE webhook_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            delivery_guid text NOT NULL UNIQUE,
            status text NOT NULL DEFAULT 'pending',
            payload jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            processed_at timestamptz
        )
        """
    )

    # ── transcript_segments: comprehension flip is atomic with the note delta ─
    op.execute(
        """
        CREATE TABLE transcript_segments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id uuid REFERENCES meetings(id),
            text text,
            status text NOT NULL DEFAULT 'pending',
            note text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    for table in (
        "transcript_segments",
        "webhook_events",
        "staged_drafts",
        "meeting_cost",
        "operation_runs",
        "sessions",
        "meetings",
        "repos",
        "users",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
