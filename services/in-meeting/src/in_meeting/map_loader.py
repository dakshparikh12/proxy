"""Meeting map loader — the map→context integration seam (MAP-LOAD, SPEC §12).

The in-meeting engine takes the pre-meeting ``index.md`` map as an injected ``map_text``
param (``context.build_turn_input``); this module is the ONE seam that loads that map from
the durable store for a meeting. A meeting is PINNED to a sha, so the load is the exact
``(tenant_id, repo, pinned_sha)`` row — never "latest" — via
:func:`premeeting.map_store.load_map`, which is ALWAYS tenant-filtered (PM-STORE-02): a
cross-tenant read is unrepresentable, and a miss (unindexed repo, or a sha with no built
map) returns ``None`` so the engine degrades honestly to a prime-only prefix (D-032).

A thin adapter over the existing store — no new SQL, no new store logic lives here.
"""
from __future__ import annotations

from typing import Any

from premeeting import map_store


async def load_meeting_map(
    *, conn: Any, tenant_id: str, repo: str, pinned_sha: str
) -> str | None:
    """Load the exact stored ``index.md`` map for this meeting's pinned key, or ``None``.

    ``conn`` is a borrowed asyncpg connection (the same shape ``premeeting.map_store``
    takes). Returns the byte-exact map text for ``(tenant_id, repo, pinned_sha)``; ``None``
    when no map is stored for that exact key (honest no-map — the engine already handles
    ``map_text=None``). Never substitutes another sha's or another tenant's map.
    """
    # ``premeeting`` ships no ``py.typed`` marker, so from this package's strict check the
    # call is ``Any``; pin the seam to the store's documented ``str | None`` contract.
    text: str | None = await map_store.load_map(conn, tenant_id=tenant_id, repo=repo, sha=pinned_sha)
    return text


__all__ = ["load_meeting_map"]
