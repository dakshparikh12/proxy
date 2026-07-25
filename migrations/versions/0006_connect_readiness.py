"""connect_readiness — the durable readiness row the connect page poll reads (§2.7)

Revision ID: 0006_connect_readiness
Revises: 0005_webhook_events_canonical
Create Date: 2026-07-25

The connect page (Doc 08 §2.7) is one out-of-meeting door served by ``control_plane``,
which runs as an autoscaling multi-instance Cloud Run service (CANONICAL-DECISIONS.md
§300). Readiness therefore CANNOT live in a per-instance in-process dict: a poll can hit
a different instance than the one running the connect→index trigger, and all state is
lost on instance recycle. The acceptance criteria (AC-CONN-008/010/020) require the
readiness value the ``GET /connect/status`` poll returns to be sourced from a DURABLE
Postgres row populated by the indexing pipeline — the substrate is the source of truth,
never a rebuildable cache (CLAUDE.md §"Source of truth vs cache").

This revision adds the ``connect_readiness`` table: one row per connect install, keyed by
the opaque ``install_id`` poll handle (a uuid4 minted server-side, NOT an authorization
token). ``status`` is constrained to the canonical Readiness enum (CANONICAL §1.5) — there
is deliberately NO ``mapping`` value, so a 'mapping' state is unrepresentable at the
substrate. ``coverage_pct`` is the REAL indexed/(indexed+flagged) fraction the pipeline
computed; ``flagged``/``gaps``/``states`` are jsonb so the honest happy-path detail and the
named not_ready gaps survive an instance recycle.
"""
from __future__ import annotations

from alembic import op

revision = "0006_connect_readiness"
down_revision = "0005_webhook_events_canonical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE connect_readiness (
            install_id   text PRIMARY KEY,
            tenant_id    text NOT NULL,
            repo_url     text NOT NULL,
            status       text NOT NULL DEFAULT 'connecting'
                         CHECK (status IN
                             ('connecting', 'cloning', 'indexing', 'ready', 'not_ready')),
            coverage_pct double precision NOT NULL DEFAULT 0,
            flagged      jsonb NOT NULL DEFAULT '[]'::jsonb,
            gaps         jsonb NOT NULL DEFAULT '[]'::jsonb,
            states       jsonb NOT NULL DEFAULT '["connecting"]'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The poll reads a single row by its opaque handle; the PK index already serves it.
    # A tenant-scoped index keeps a per-tenant sweep cheap (isolation reachability).
    op.execute(
        "CREATE INDEX connect_readiness_tenant_idx ON connect_readiness (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connect_readiness CASCADE")
