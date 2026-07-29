"""Durable, tenant-isolated map store — the ``repo_maps`` Postgres table (PM-STORE-01..03).

The ``index.md`` repo map is the pre-meeting system's ONE durable derived artifact, so it lives
in Postgres (not a host-local file): readable from ANY instance, surviving host recycle. This
module carries the parameterised SQL + the clean downstream API:

  * :func:`save_map` — upsert the map bytes for ``(tenant, repo, sha)``.
  * :func:`load_map` — read them back, ALWAYS filtered by ``tenant_id`` so a cross-tenant read
    is impossible (PM-STORE-02); a miss returns ``None`` (fail-closed).

Both take a borrowed asyncpg connection (the same shape ``db.repos.*`` use) so the async wake /
Workroom resolvers compose with ``Database.acquire()``; :class:`MapStore` is the thin object the
pipeline + consumers hold, resolving a connection per call so a poll on a different Cloud Run
instance reads the same live row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


async def save_map(conn: Any, *, tenant_id: str, repo: str, sha: str, map_text: str) -> None:
    """Upsert the map text for ``(tenant_id, repo, sha)`` (idempotent on the PK).

    A re-build at the same SHA overwrites the prior map + re-stamps ``built_at`` (a re-verify
    after a fix produces a fresh map for the same commit)."""
    await conn.execute(
        """
        INSERT INTO repo_maps (tenant_id, repo, sha, map, built_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (tenant_id, repo, sha)
        DO UPDATE SET map = EXCLUDED.map, built_at = now()
        """,
        tenant_id,
        repo,
        sha,
        map_text,
    )


async def load_map(conn: Any, *, tenant_id: str, repo: str, sha: str) -> str | None:
    """Read the exact map bytes for ``(tenant_id, repo, sha)``, or ``None`` on a miss.

    ALWAYS filtered by ``tenant_id`` — the query can never return another tenant's row even for
    the same ``(repo, sha)`` (PM-STORE-02). Byte-exact round-trip with :func:`save_map`."""
    row = await conn.fetchrow(
        "SELECT map FROM repo_maps WHERE tenant_id = $1 AND repo = $2 AND sha = $3",
        tenant_id,
        repo,
        sha,
    )
    return None if row is None else str(row["map"])


async def load_latest_map(conn: Any, *, tenant_id: str, repo: str) -> tuple[str, str] | None:
    """The most-recently-built map for ``(tenant_id, repo)`` as ``(sha, map_text)``, or ``None``.

    The wake / Workroom mounts want "the current map for this repo" without knowing the exact
    SHA the meeting pinned; this returns the freshest, still tenant-scoped."""
    row = await conn.fetchrow(
        """
        SELECT sha, map FROM repo_maps
         WHERE tenant_id = $1 AND repo = $2
         ORDER BY built_at DESC
         LIMIT 1
        """,
        tenant_id,
        repo,
    )
    return None if row is None else (str(row["sha"]), str(row["map"]))


@dataclass
class MapStore:
    """The pipeline / consumer handle over the durable ``repo_maps`` table.

    Holds a ``libs.db.Database`` (the async pool) and resolves a fresh connection per call so a
    write from the connect trigger and a read from a live meeting on another instance hit the
    same durable row. Tenant-isolated by construction (every method threads ``tenant_id``)."""

    db: Any  # libs.db.Database

    async def save(self, *, tenant_id: str, repo: str, sha: str, map_text: str) -> None:
        async with self.db.acquire() as conn:
            await save_map(conn, tenant_id=tenant_id, repo=repo, sha=sha, map_text=map_text)

    async def load(self, *, tenant_id: str, repo: str, sha: str) -> str | None:
        async with self.db.acquire() as conn:
            return await load_map(conn, tenant_id=tenant_id, repo=repo, sha=sha)

    async def load_latest(self, *, tenant_id: str, repo: str) -> tuple[str, str] | None:
        async with self.db.acquire() as conn:
            return await load_latest_map(conn, tenant_id=tenant_id, repo=repo)


__all__ = ["MapStore", "load_latest_map", "load_map", "save_map"]
