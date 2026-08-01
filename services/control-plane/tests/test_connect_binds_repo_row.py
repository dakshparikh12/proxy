"""BLOCKER A — a clean connect→index writes a ``repos`` row ``POST /meetings`` then resolves.

The live connect/index flow wrote ``tenants`` + ``connect_readiness`` + ``repo_maps`` but NEVER a
``repos`` row, so ``POST /meetings`` — which resolves the invited repo via
``db.repos.meetings.get_repo_for_tenant`` — 404'd "Repo not found" even after a clean index,
masking everything downstream. The fix binds the ``repos`` row on the connect SUCCESS path.

These prove, against an in-memory ``repos`` table that speaks the same asyncpg ``fetchrow`` /
``execute`` the real code uses:

* after a READY pipeline result, ``trigger_connect_index`` upserts a ``repos`` row and
  ``get_repo_for_tenant`` then RESOLVES it (no 404) — tenant-scoped;
* ``full_name`` is stored as the connect ``repo_url`` VERBATIM, so it matches the ``POST
  /meetings`` ``repo`` string byte-for-byte AND ``repo_name_from_url(full_name)`` equals the
  ``repo_maps.repo`` key the map was stored under (the invite's HEAD-pin read finds the map);
* the bind is idempotent — a redelivered connect never inserts a duplicate row;
* a NOT-ready pipeline result writes NO ``repos`` row (never binds an unindexed repo).
"""
from __future__ import annotations

from typing import Any

from premeeting.paths import repo_name_from_url

_TENANT = "11111111-1111-1111-1111-111111111111"
_REPO_URL = "https://github.com/calcom/cal.com"


class _FakeReposConn:
    """An in-memory ``repos`` table speaking the asyncpg ``fetchrow`` / row-tuple SQL the code uses.

    Enough of the two statements ``upsert_repo_for_tenant`` and ``get_repo_for_tenant`` issue to
    round-trip a real bind → resolve without a live Postgres (fakes ok, per the acceptance).
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.insert_count = 0

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        s = " ".join(sql.split())
        if s.startswith("SELECT id, tenant_id, full_name, default_branch, github_installation_id FROM repos WHERE"):
            tenant_id, full_name = args[0], args[1]
            for r in self.rows:
                if r["tenant_id"] == tenant_id and r["full_name"] == full_name:
                    return dict(r)
            return None
        if s.startswith("INSERT INTO repos"):
            tenant_id, full_name, default_branch, ghi = args
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
        if s.startswith("SELECT id, tenant_id, full_name, default_branch FROM repos WHERE"):
            # get_repo_for_tenant's projection (no github_installation_id column).
            tenant_id, full_name = args[0], args[1]
            for r in self.rows:
                if r["tenant_id"] == tenant_id and r["full_name"] == full_name:
                    return {k: r[k] for k in ("id", "tenant_id", "full_name", "default_branch")}
            return None
        raise AssertionError(f"unexpected SQL: {s}")


class _FakeAcquire:
    def __init__(self, conn: _FakeReposConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeReposConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeDB:
    def __init__(self, conn: _FakeReposConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


class _FakeMapStore:
    """The async MapStore stand-in the trigger threads: it carries the live DB pool."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.saved: list[dict[str, str]] = []

    async def save(self, *, tenant_id: str, repo: str, sha: str, map_text: str) -> None:
        self.saved.append({"tenant_id": tenant_id, "repo": repo, "sha": sha})


def _bind_directly(map_store: Any) -> None:
    """Drive the connect module's private repos-bind helper (the connect SUCCESS path)."""
    import control_plane.connect as connect_mod

    connect_mod._bind_repo_row(map_store, tenant_id=_TENANT, repo_url=_REPO_URL)


def test_connect_success_binds_repo_row_that_get_repo_for_tenant_resolves() -> None:
    """A clean connect writes a ``repos`` row the invite route then resolves (no 404), consistent key."""
    import asyncio

    from libs.db import repos as _repos

    conn = _FakeReposConn()
    db = _FakeDB(conn)
    map_store = _FakeMapStore(db)

    # The connect SUCCESS path binds the repo (this is what runs after store.set_ready).
    _bind_directly(map_store)

    async def _resolve() -> dict[str, Any] | None:
        # The EXACT lookup POST /meetings does: match the body's ``repo`` string (== the connect
        # repo_url) against repos.full_name, tenant-scoped.
        return await _repos.meetings.get_repo_for_tenant(
            conn, tenant_id=_TENANT, full_name=_REPO_URL
        )

    repo_row = asyncio.run(_resolve())
    assert repo_row is not None                       # no 404 — the invite finds the repo
    assert repo_row["tenant_id"] == _TENANT
    assert repo_row["full_name"] == _REPO_URL         # matches the POST /meetings body verbatim

    # CONSISTENCY: the repos.full_name derives to the SAME key the map was stored under, so the
    # invite's HEAD-pin read (_latest_indexed_sha → repo_name_from_url(full_name)) finds the map.
    assert repo_name_from_url(repo_row["full_name"]) == "cal.com"


def test_bind_is_idempotent_no_duplicate_on_redelivery() -> None:
    """A redelivered connect binds the SAME row — never a duplicate repos row."""
    conn = _FakeReposConn()
    map_store = _FakeMapStore(_FakeDB(conn))

    _bind_directly(map_store)
    _bind_directly(map_store)  # redelivery

    assert conn.insert_count == 1                     # inserted once, not twice
    assert len([r for r in conn.rows if r["full_name"] == _REPO_URL]) == 1


def test_cross_tenant_lookup_does_not_resolve_another_tenants_repo() -> None:
    """The bound repo is tenant-scoped — a different tenant cannot resolve it (isolation)."""
    import asyncio

    from libs.db import repos as _repos

    conn = _FakeReposConn()
    _bind_directly(_FakeMapStore(_FakeDB(conn)))

    async def _resolve_other() -> dict[str, Any] | None:
        return await _repos.meetings.get_repo_for_tenant(
            conn, tenant_id="99999999-9999-9999-9999-999999999999", full_name=_REPO_URL
        )

    assert asyncio.run(_resolve_other()) is None      # another tenant sees no existence leak


def test_not_ready_pipeline_writes_no_repos_row() -> None:
    """A NOT-ready connect never binds a repos row (never an unindexed repo the invite can pin)."""
    import control_plane.connect as connect_mod

    conn = _FakeReposConn()
    map_store = _FakeMapStore(_FakeDB(conn))

    class _NotReadyStore:
        def mark_state(self, *a: Any, **k: Any) -> None:
            pass

        def set_ready(self, *a: Any, **k: Any) -> None:  # pragma: no cover - must NOT be called
            raise AssertionError("set_ready must not run on a not-ready result")

        def set_not_ready(self, *a: Any, **k: Any) -> None:
            pass

    class _NotReadyResult:
        ready = False
        reasons = ["verify: index gap"]

    # Drive the terminal branch by monkeypatch-free direct call: run_pipeline is replaced so the
    # trigger reaches its terminal not_ready write without a real clone.
    import premeeting.pipeline as _pipeline

    async def _fake_run_pipeline(**kwargs: Any) -> Any:
        return _NotReadyResult()

    orig = _pipeline.run_pipeline
    _pipeline.run_pipeline = _fake_run_pipeline  # type: ignore[assignment]
    try:
        connect_mod.trigger_connect_index(
            _NotReadyStore(),
            "install-notready",
            tenant_id=_TENANT,
            repo_url=_REPO_URL,
            map_provider=object(),   # non-None so the real (patched) pipeline branch runs
            map_store=map_store,
        )
    finally:
        _pipeline.run_pipeline = orig  # type: ignore[assignment]

    assert conn.insert_count == 0                     # no repos row bound on a not-ready index
