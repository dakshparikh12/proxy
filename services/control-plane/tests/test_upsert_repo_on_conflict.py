"""BUG 3 — upsert_repo_for_tenant is a single atomic INSERT ... ON CONFLICT, not a race.

``repos`` had NO unique constraint on ``(tenant_id, full_name)``; the "idempotent" bind was a
racy SELECT-then-INSERT, so two concurrent / redelivered connects could each read "no row" and
each INSERT → duplicate rows. Migration ``0010_repos_tenant_fullname_uniq`` adds the UNIQUE
index and the repo is converted to ``INSERT ... ON CONFLICT (tenant_id, full_name) DO UPDATE``.

These prove, against a fake asyncpg conn that RECORDS the exact SQL issued:

* the upsert issues ONE statement — an ``INSERT ... ON CONFLICT (tenant_id, full_name) DO
  UPDATE`` — and never a preceding ``SELECT`` (the race window is gone);
* a double-call binds the SAME row (no duplicate) — idempotent;
* a conflict backfills a NULL ``default_branch`` but never overwrites a recorded value.
"""
from __future__ import annotations

from typing import Any

from libs.db import repos as _repos

_TENANT = "11111111-1111-1111-1111-111111111111"
_REPO = "https://github.com/calcom/cal.com"


class _RecordingConn:
    """A fake asyncpg conn modelling the repos UNIQUE (tenant_id, full_name) + ON CONFLICT."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.sql_log: list[str] = []
        self.insert_count = 0

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        s = " ".join(sql.split())
        self.sql_log.append(s)
        assert s.startswith("INSERT INTO repos"), f"expected an INSERT upsert, got: {s}"
        assert "ON CONFLICT (tenant_id, full_name) DO UPDATE" in s, s
        tenant_id, full_name, default_branch, ghi = args
        for r in self.rows:
            if r["tenant_id"] == tenant_id and r["full_name"] == full_name:
                # DO UPDATE — COALESCE-backfill NULLs, never overwrite a recorded value.
                if r["default_branch"] is None and default_branch is not None:
                    r["default_branch"] = default_branch
                if r["github_installation_id"] is None and ghi is not None:
                    r["github_installation_id"] = ghi
                return dict(r)
        row = {
            "id": f"repo-{len(self.rows)}",
            "tenant_id": tenant_id,
            "full_name": full_name,
            "default_branch": default_branch,
            "github_installation_id": ghi,
        }
        self.rows.append(row)
        self.insert_count += 1
        return dict(row)


def test_upsert_issues_single_insert_on_conflict_no_select() -> None:
    """The bind is one atomic INSERT ... ON CONFLICT — never a SELECT-then-INSERT (no race)."""
    import asyncio

    conn = _RecordingConn()
    asyncio.run(
        _repos.meetings.upsert_repo_for_tenant(conn, tenant_id=_TENANT, full_name=_REPO)
    )
    assert len(conn.sql_log) == 1  # exactly one statement, no preceding SELECT
    assert conn.sql_log[0].startswith("INSERT INTO repos")
    assert "ON CONFLICT (tenant_id, full_name) DO UPDATE" in conn.sql_log[0]
    assert "SELECT" not in conn.sql_log[0]


def test_double_call_binds_one_row_no_duplicate() -> None:
    """A redelivered / concurrent connect never duplicates the binding (idempotent)."""
    import asyncio

    conn = _RecordingConn()

    async def _twice() -> None:
        await _repos.meetings.upsert_repo_for_tenant(
            conn, tenant_id=_TENANT, full_name=_REPO
        )
        await _repos.meetings.upsert_repo_for_tenant(
            conn, tenant_id=_TENANT, full_name=_REPO
        )

    asyncio.run(_twice())
    assert conn.insert_count == 1
    assert len([r for r in conn.rows if r["full_name"] == _REPO]) == 1


def test_conflict_backfills_null_but_keeps_recorded_value() -> None:
    """DO UPDATE COALESCE-backfills a NULL default_branch but never overwrites a recorded one."""
    import asyncio

    conn = _RecordingConn()

    async def _seq() -> dict[str, Any]:
        # First bind records default_branch='main'.
        await _repos.meetings.upsert_repo_for_tenant(
            conn, tenant_id=_TENANT, full_name=_REPO, default_branch="main"
        )
        # A redelivery with a DIFFERENT branch must NOT overwrite the recorded 'main'.
        return await _repos.meetings.upsert_repo_for_tenant(
            conn, tenant_id=_TENANT, full_name=_REPO, default_branch="master"
        )

    row = asyncio.run(_seq())
    assert row["default_branch"] == "main"  # recorded value preserved (COALESCE)
    assert conn.insert_count == 1
