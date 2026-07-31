"""repo_maps — the durable per-tenant/repo/SHA index.md map (PM-STORE-01..04)

Revision ID: 0009_repo_maps
Revises: 0008_substrate_schema_gaps
Create Date: 2026-07-27

The pre-meeting system's ONE durable artifact is a dense ``index.md`` repo MAP. The clone +
any structural cache is a rebuildable derived cache (CLAUDE.md §"Source of truth vs cache");
the MAP is the durable derived artifact and therefore lives in Postgres, readable from ANY
instance and surviving host recycle (PM-STORE-03), never a host-local file.

One row per ``(tenant_id, repo, sha)``: ``map`` is the index.md text, ``built_at`` the wall
clock the map-build finished. ``tenant_id`` is a DECLARED FK to ``tenants(id)`` (the same
tenancy-root reachability every other durable tenant-scoped table has — R-INV-09 / AC-TEN-001)
so a map is always tenant-scoped and a cross-tenant read is unrepresentable at the substrate
(PM-STORE-02): the PK carries ``tenant_id`` first, so ``load_map(tenant, repo, sha)`` for
tenant B can never return tenant A's row even for the same repo/sha.

Applying to PROD is human-gated (never auto-applied); this revision is proven to upgrade AND
downgrade cleanly on a scratch DB (PM-STORE-04).
"""
from __future__ import annotations

from alembic import op

revision = "0009_repo_maps"
down_revision = "0008_substrate_schema_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE repo_maps (
            tenant_id  uuid NOT NULL REFERENCES tenants(id),
            repo       text NOT NULL,
            sha        text NOT NULL,
            map        text NOT NULL,
            built_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, repo, sha)
        )
        """
    )
    # A per-(tenant,repo) index serves "the latest map for this repo" lookups cheaply and keeps
    # a per-tenant sweep tenant-local (isolation reachability).
    op.execute("CREATE INDEX repo_maps_tenant_repo_idx ON repo_maps (tenant_id, repo)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS repo_maps CASCADE")
