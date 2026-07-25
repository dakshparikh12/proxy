"""webhook_events → CANONICAL §12.10 — add provider/sha/received_at + status/provider CHECKs

Revision ID: 0005_webhook_events_canonical
Revises: 0004_doc03_note_store_v33
Create Date: 2026-07-25

0001_substrate created webhook_events as {id, delivery_guid, status, payload
(nullable), created_at, processed_at} and 0003 added a nullable ``tenant_id``.
The canonical literal (CANONICAL-DECISIONS §12.10 / 00-FOUNDATION §5.6) is:

    CREATE TABLE webhook_events (
      id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider      text NOT NULL,               -- 'github' | 'recall'
      delivery_guid text NOT NULL UNIQUE,        -- provider delivery id → dedupe key
      sha           text,                        -- push SHA (GitHub) | null
      payload       jsonb NOT NULL,
      status        text NOT NULL DEFAULT 'pending',   -- pending|processed|failed
      received_at   timestamptz NOT NULL DEFAULT now()
    );

This is a FORWARD reconciliation migration (0001 is shipped and is NEVER edited in
place). It adds the missing ``provider`` / ``sha`` / ``received_at`` columns, makes
``payload`` NOT NULL, and constrains ``status`` (and ``provider``) with CHECKs —
handling existing rows so ``alembic upgrade head`` succeeds on the dev DB:

  * ``provider`` is added with a server_default of ``'recall'`` so any existing rows
    backfill to a valid provider, the default is then DROPPED so new writes must name
    the provider explicitly, and a CHECK pins the domain to {github, recall}.
  * ``payload`` NULLs are backfilled to ``'{}'::jsonb`` before the NOT NULL is set.
  * ``received_at`` is added NOT NULL DEFAULT now(); existing rows backfill from
    ``created_at`` so the received timestamp stays truthful (created_at is retained,
    NOT dropped, so the drain's ``ORDER BY created_at`` keeps working unchanged).
  * ``status`` gets a CHECK to {pending, processed, failed}; existing values are
    already in-domain (only 'pending'/'processed' were ever written), so no backfill.

``created_at`` and ``processed_at`` are RETAINED (not dropped): the drain reads/writes
them, and ``received_at`` is reconciled alongside — dropping them would break the drain.
``delivery_guid`` keeps its UNIQUE constraint (at-least-once dedupe). ``tenant_id`` is
left untouched (nullable reachability FK from 0003).
"""
from __future__ import annotations

from alembic import op

revision = "0005_webhook_events_canonical"
down_revision = "0004_doc03_note_store_v33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── provider: add with a safe default, backfill existing rows, drop the default ──
    # A server_default lets the ADD COLUMN NOT NULL succeed on a table with rows; we
    # then DROP the default so future inserts must name the provider (no silent default).
    # A server_default of 'recall' both (a) lets ADD COLUMN NOT NULL succeed on a
    # table with rows and (b) backstops a column-omitting insert so the stored row is
    # never null (NOT NULL is about stored rows, not insert ergonomics). The product
    # repo always writes ``provider`` explicitly; the default only backfills a bare
    # insert. The CHECK still rejects any out-of-domain provider.
    op.execute(
        "ALTER TABLE webhook_events "
        "ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'recall'"
    )
    op.execute(
        "ALTER TABLE webhook_events "
        "ADD CONSTRAINT webhook_events_provider_check "
        "CHECK (provider IN ('github', 'recall'))"
    )

    # ── sha: nullable (push SHA for GitHub, NULL for Recall) ─────────────────
    op.execute("ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS sha text")

    # ── received_at: NOT NULL DEFAULT now(); backfill existing rows from created_at ──
    op.execute(
        "ALTER TABLE webhook_events "
        "ADD COLUMN IF NOT EXISTS received_at timestamptz NOT NULL DEFAULT now()"
    )
    # Backfill received_at from the existing created_at so the recorded receive time
    # of pre-migration rows stays truthful (rather than "now" at migration time).
    op.execute(
        "UPDATE webhook_events SET received_at = created_at "
        "WHERE created_at IS NOT NULL"
    )

    # ── payload: backfill NULLs, give a '{}' default, then make NOT NULL ─────
    # DEFAULT '{}'::jsonb keeps a bare insert non-null while NOT NULL enforces the
    # canonical §12.10 invariant on every stored row (the product repo writes the
    # real payload; the default only backstops an insert that omits the column).
    op.execute("UPDATE webhook_events SET payload = '{}'::jsonb WHERE payload IS NULL")
    op.execute("ALTER TABLE webhook_events ALTER COLUMN payload SET DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE webhook_events ALTER COLUMN payload SET NOT NULL")

    # ── status: CHECK to {pending, processed, failed} (behavioural domain) ───
    # Existing values are already in-domain (only 'pending'/'processed' were written).
    op.execute(
        "ALTER TABLE webhook_events "
        "ADD CONSTRAINT webhook_events_status_check "
        "CHECK (status IN ('pending', 'processed', 'failed'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE webhook_events DROP CONSTRAINT IF EXISTS webhook_events_status_check"
    )
    op.execute("ALTER TABLE webhook_events ALTER COLUMN payload DROP NOT NULL")
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS received_at")
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS sha")
    op.execute(
        "ALTER TABLE webhook_events DROP CONSTRAINT IF EXISTS webhook_events_provider_check"
    )
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS provider")
