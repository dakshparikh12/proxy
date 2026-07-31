"""Resolve a meeting's ``code_intel`` grounding context from the durable substrate.

The wake turn and the Workroom both advertise ``mcp__code_intel__*`` tools but need the
meeting's tenant graph + clone to mount the server that provides them. This module is the ONE
db-backed resolver that turns a meeting's identity (its resolved bot row, or just its
``meeting_id``) into a :class:`code_intel.sdk_server.CodeIntelContext` (the durable per-repo
``graph.db`` + ``checkout`` paths) — the SAME lookup ``webhooks._resolve_referent_corpus``
already does for the Scribe's referent corpus (repo_id -> repos.get_repo_by_id -> tenant_id +
full_name -> ``tenant_repo_dir/graph.db``).

Fail-closed by construction (§3.8 / Rule 6): a meeting with no repo, an unknown repo, or an
unindexed repo yields ``None`` — the caller then mounts NO code_intel server and Proxy degrades
honestly (it still wakes; it just has no codebase tools this meeting), never a crash and never a
cross-tenant read.
"""
from __future__ import annotations

from typing import Any

from libs.db import Database, repos


async def resolve_code_intel_context_from_row(
    resolved: dict[str, Any], *, db: Database
) -> Any | None:
    """Resolve the meeting's :class:`CodeIntelContext` from an already-resolved bot row.

    ``resolved`` is the ``meetings`` row (``id``/``tenant_id``/``repo_id``) the provisioner
    already fetched via ``repos.meetings.get_by_bot_id`` — so the join path resolves the context
    without a second bot lookup. Returns ``None`` when the meeting has no repo bound (fail
    closed)."""
    repo_id = resolved.get("repo_id")
    if repo_id is None:
        return None
    return await _context_for_repo(repo_id, db=db)


async def resolve_code_intel_context_for_meeting(
    meeting_id: str, *, db: Database
) -> Any | None:
    """Resolve the meeting's :class:`CodeIntelContext` from just its ``meeting_id``.

    The Workroom path carries only the ``meeting_id`` (``bundle.notes_ref``); this reads the
    meeting row (``repos.meetings.get_by_id``) to recover its ``repo_id``, then the repo's
    tenant + name. Returns ``None`` when the meeting/repo is unknown (fail closed)."""
    async with db.acquire() as conn:
        meeting = await repos.meetings.get_by_id(conn, meeting_id)
    if meeting is None or meeting.get("repo_id") is None:
        return None
    return await _context_for_repo(meeting["repo_id"], db=db)


async def resolve_map_text_from_row(resolved: dict[str, Any], *, db: Database) -> str | None:
    """Resolve the pre-meeting MAP (``index.md``) text for a meeting's repo, or ``None``.

    Loads the latest durable map the pre-meeting system stored in Postgres ``repo_maps`` for this
    meeting's ``(tenant, repo)`` — the SAME identity the code_intel context resolves from. The
    live wake turn mounts it as an orientation prefix. Fail-closed (Rule 6): a meeting with no
    repo, an unmapped repo, or any resolution fault yields ``None`` — the wake turn is unaffected
    (it still wakes; it just has no map to prime on). Never a cross-tenant read: the load is
    ALWAYS scoped to this repo's ``tenant_id``."""
    repo_id = resolved.get("repo_id")
    if repo_id is None:
        return None
    try:
        from code_intel.paths import repo_name_from_url
        from premeeting.map_store import load_latest_map

        async with db.acquire() as conn:
            repo = await repos.meetings.get_repo_by_id(conn, repo_id)
        if repo is None or not repo.get("full_name"):
            return None
        repo_name = repo_name_from_url(str(repo["full_name"]))
        async with db.acquire() as conn:
            latest = await load_latest_map(conn, tenant_id=str(repo["tenant_id"]), repo=repo_name)
        return None if latest is None else latest[1]
    except Exception:  # noqa: BLE001 - Rule 6: a resolution fault degrades to no map, never a crash
        return None


async def _context_for_repo(repo_id: Any, *, db: Database) -> Any | None:
    """Resolve a repo id → its tenant + name → the durable index/clone context (fail closed)."""
    from code_intel.paths import repo_name_from_url
    from code_intel.sdk_server import CodeIntelContext

    async with db.acquire() as conn:
        repo = await repos.meetings.get_repo_by_id(conn, repo_id)
    if repo is None or not repo.get("full_name"):
        return None
    repo_name = repo_name_from_url(str(repo["full_name"]))
    return CodeIntelContext.for_tenant_repo(
        tenant_id=str(repo["tenant_id"]), repo_name=repo_name
    )


__all__ = [
    "resolve_code_intel_context_for_meeting",
    "resolve_code_intel_context_from_row",
    "resolve_map_text_from_row",
]
