"""repos — UNIQUE (tenant_id, full_name) so the connect bind is a real idempotent upsert.

Revision ID: 0010_repos_tenant_fullname_uniq
Revises: 0009_repo_maps
Create Date: 2026-08-01

The connect SUCCESS path binds the tenant's ``repos`` row (``upsert_repo_for_tenant``) so
``POST /meetings`` can resolve the invited repo. But ``repos`` had NO unique constraint on
``(tenant_id, full_name)``, so the "idempotent" bind was a racy SELECT-then-INSERT: two
concurrent connects (or a redelivered install racing itself) each read "no row" and each
INSERT, producing DUPLICATE rows for the same tenant+repo. ``get_repo_for_tenant`` then
resolves ambiguously and the invite pins an arbitrary one.

This adds the missing UNIQUE INDEX so the bind can use ``INSERT ... ON CONFLICT (tenant_id,
full_name) DO UPDATE`` — a single atomic statement, no race window. Any pre-existing
duplicates are de-duped first (keep the earliest ``created_at`` row per group, drop the rest)
so the index creates cleanly on a migrated dev DB. Meetings reference ``repos.id`` (which the
kept row preserves), so collapsing duplicates never orphans a meeting.

Applying to PROD is human-gated (never auto-applied); this revision upgrades AND downgrades
cleanly on a scratch DB.
"""
from __future__ import annotations

from alembic import op

revision = "0010_repos_tenant_fullname_uniq"
down_revision = "0009_repo_maps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # De-dupe any existing rows so the UNIQUE index can be created: keep the earliest row per
    # (tenant_id, full_name) group, delete the later duplicates. ``ctid`` disambiguates rows that
    # share created_at. Meetings point at repos.id — the kept (earliest) row's id survives, so no
    # meeting is orphaned by the collapse.
    op.execute(
        """
        DELETE FROM repos r
        USING (
            SELECT tenant_id, full_name,
                   (ARRAY_AGG(id ORDER BY created_at, ctid))[1] AS keep_id
              FROM repos
             GROUP BY tenant_id, full_name
            HAVING COUNT(*) > 1
        ) dup
        WHERE r.tenant_id = dup.tenant_id
          AND r.full_name = dup.full_name
          AND r.id <> dup.keep_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX repos_tenant_fullname_uniq ON repos (tenant_id, full_name)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS repos_tenant_fullname_uniq")
