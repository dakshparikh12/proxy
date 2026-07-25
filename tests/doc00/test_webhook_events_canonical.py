"""webhook_events canonical schema — the CANONICAL §12.10 literal, on the real DB.

Node: foundation.webhook-events-schema-fix (AC-SUB-022/023/024, R-DOC00-5-16).

0001_substrate created webhook_events as {id, delivery_guid, status, payload
(nullable), created_at, processed_at}; 0003 added tenant_id. The canonical literal
(CANONICAL §12.10) is {id, provider('github'|'recall'), delivery_guid UNIQUE, sha,
payload NOT NULL, status DEFAULT 'pending' (pending|processed|failed), received_at}.
A NEW forward migration (0005) closes this drift WITHOUT editing a shipped
migration. These tests run on the migrated Postgres (the real product path):

  * the canonical column set + NOT NULLs are present after ``alembic upgrade head``;
  * the status CHECK REJECTS status='invalid' (behaviour, not just column existence);
  * the provider CHECK REJECTS an out-of-domain provider;
  * delivery_guid stays UNIQUE (duplicate delivery is a no-op);
  * the product repo (``insert_event`` → ``list_pending`` → ``mark_processed``) drains
    pending rows idempotently on the canonical schema.

Product imports live INSIDE the bodies so this module COLLECTS clean and is red
before the product exists; ``S.pg_conn()`` SKIPS when no local Postgres is present.
"""
from __future__ import annotations

import pytest

import _support as S

# The canonical webhook_events column set (CANONICAL §12.10 literal + the
# tenant_id reachability column 0003 added — kept for invariant 9).
_WEBHOOK_COLS = {
    "id", "provider", "delivery_guid", "sha", "payload", "status",
    "received_at", "created_at", "processed_at", "tenant_id",
}
_WEBHOOK_STATUS_DOMAIN = {"pending", "processed", "failed"}
_WEBHOOK_PROVIDER_DOMAIN = {"github", "recall"}


def _migrate(conn) -> None:
    r = S.apply_migrations(S._local_dsn() or "")
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"


# ── AC-SUB-022 (canonical columns + UNIQUE delivery_guid dedupe) ─────────────
@pytest.mark.contract
def test_webhook_events_canonical_columns_and_notnulls():
    """The migrated webhook_events shows the canonical §12.10 columns + NOT NULLs."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        _migrate(conn)
        assert S.table_exists(conn, "webhook_events"), "webhook_events must exist"

        cols = S.table_columns(conn, "webhook_events")
        missing = _WEBHOOK_COLS - set(cols)
        assert not missing, f"webhook_events missing canonical columns: {missing}"

        # provider/received_at exist and payload/provider/received_at are NOT NULL.
        nulls = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name='webhook_events'"
            ).fetchall()
        }
        assert nulls["provider"] == "NO", "provider must be NOT NULL"
        assert nulls["payload"] == "NO", "payload must be NOT NULL (canonical §12.10)"
        assert nulls["received_at"] == "NO", "received_at must be NOT NULL"
        assert nulls["sha"] == "YES", "sha is nullable (push SHA | null)"


@pytest.mark.model_stateful
def test_webhook_events_delivery_guid_unique_dedupe():
    """A duplicate delivery_guid is a no-op (at-least-once dedupe stays intact)."""
    import libs.db  # noqa: F401
    with S.pg_conn() as conn:
        _migrate(conn)
        conn.execute("DELETE FROM webhook_events")
        ins = (
            "INSERT INTO webhook_events (provider, delivery_guid, payload, status) "
            "VALUES ('github', %s, '{}'::jsonb, 'pending') "
            "ON CONFLICT (delivery_guid) DO NOTHING"
        )
        conn.execute(ins, ("guid-canon",))
        conn.execute(ins, ("guid-canon",))  # duplicate delivery → no-op
        n = conn.execute(
            "SELECT count(*) FROM webhook_events WHERE delivery_guid='guid-canon'"
        ).fetchone()[0]
        assert n == 1, f"duplicate delivery_guid must yield exactly one row; got {n}"


# ── AC-SUB-023 (status + provider CHECK constraints REJECT bad values) ───────
@pytest.mark.model_stateful
def test_webhook_events_status_check_rejects_invalid():
    """The status CHECK REJECTS status='invalid' (behaviour, not column existence)."""
    import psycopg

    import libs.db  # noqa: F401
    with S.pg_conn() as conn:
        _migrate(conn)
        conn.execute("DELETE FROM webhook_events")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO webhook_events (provider, delivery_guid, payload, status) "
                "VALUES ('github', 'g-bad-status', '{}'::jsonb, 'invalid')"
            )
        # Every legal status value is accepted.
        for i, st in enumerate(sorted(_WEBHOOK_STATUS_DOMAIN)):
            conn.execute(
                "INSERT INTO webhook_events (provider, delivery_guid, payload, status) "
                "VALUES ('github', %s, '{}'::jsonb, %s)",
                (f"g-ok-{i}", st),
            )
        n = conn.execute("SELECT count(*) FROM webhook_events").fetchone()[0]
        assert n == len(_WEBHOOK_STATUS_DOMAIN), (
            f"all legal statuses must insert; got {n}"
        )


@pytest.mark.model_stateful
def test_webhook_events_provider_check_rejects_bad_provider():
    """The provider CHECK REJECTS a provider outside {github,recall}."""
    import psycopg

    import libs.db  # noqa: F401
    with S.pg_conn() as conn:
        _migrate(conn)
        conn.execute("DELETE FROM webhook_events")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO webhook_events (provider, delivery_guid, payload, status) "
                "VALUES ('gitlab', 'g-bad-provider', '{}'::jsonb, 'pending')"
            )
        for prov in sorted(_WEBHOOK_PROVIDER_DOMAIN):
            conn.execute(
                "INSERT INTO webhook_events (provider, delivery_guid, payload, status) "
                "VALUES (%s, %s, '{}'::jsonb, 'pending')",
                (prov, f"ok-{prov}"),
            )
        n = conn.execute("SELECT count(*) FROM webhook_events").fetchone()[0]
        assert n == len(_WEBHOOK_PROVIDER_DOMAIN), "both legal providers must insert"


# ── AC-SUB-024 (the product repo drains pending idempotently, canonical schema)
@pytest.mark.integration
def test_webhook_repo_drains_pending_idempotently_on_canonical_schema():
    """insert_event → list_pending → mark_processed drains pending idempotently.

    Runs the REAL product repo against the migrated canonical schema: a fresh
    delivery is inserted with provider/received_at written; the drain lists it,
    marks it processed, and a re-drain is a no-op (nothing pending left, and a
    duplicate delivery never re-inserts).
    """
    import asyncio

    from libs.db import Database, repos

    with S.pg_conn() as conn:
        _migrate(conn)
        conn.execute("DELETE FROM webhook_events")

    async def _run() -> tuple[bool, bool, int, int, str]:
        db = await Database.connect(S._local_dsn())
        try:
            async with db.acquire() as c:
                newly = await repos.webhooks.insert_event(
                    c, "drain-guid-1", {"event": "bot.in_call", "data": {"bot_id": "b1"}}
                )
                dup = await repos.webhooks.insert_event(
                    c, "drain-guid-1", {"event": "bot.in_call"}
                )
            # First drain: exactly one pending row, then marked processed.
            async with db.acquire() as c:
                pending = await repos.webhooks.list_pending(c)
            first_pending = len(pending)
            for ev in pending:
                async with db.acquire() as c:
                    await repos.webhooks.mark_processed(c, ev["id"])
            # Second drain (idempotent): nothing pending remains.
            async with db.acquire() as c:
                pending2 = await repos.webhooks.list_pending(c)
            second_pending = len(pending2)
            async with db.acquire() as c:
                prov = (
                    await c.fetchrow(
                        "SELECT provider FROM webhook_events WHERE delivery_guid='drain-guid-1'"
                    )
                )["provider"]
            return newly, dup, first_pending, second_pending, prov
        finally:
            await db.close()

    newly, dup, first_pending, second_pending, prov = asyncio.run(_run())
    assert newly is True, "the first delivery must be newly inserted"
    assert dup is False, "a duplicate delivery_guid must be a dedupe no-op"
    assert first_pending == 1, f"exactly one pending row expected; got {first_pending}"
    assert second_pending == 0, (
        f"drain must be idempotent — no pending left; got {second_pending}"
    )
    assert prov == "recall", (
        f"insert_event must write a valid provider (recall for a bot event); got {prov!r}"
    )
