"""meetings repository — the meeting bound to (tenant, repo, pinned_sha=HEAD).

The Recall bot_id is written back after the bot launches so a webhook can resolve
its bot_id → meeting → (tenant, repo).
"""
from __future__ import annotations

from typing import Any


async def insert_meeting(
    conn: Any,
    *,
    tenant_id: Any,
    repo_id: Any,
    meeting_url: str | None,
    pinned_sha: str | None,
    recall_bot_id: str | None,
    status: str = "live",
    platform: str | None = None,
) -> dict[str, Any]:
    # ``platform`` is the meeting platform (recall|zoom|teams|…) set at join (CANONICAL
    # §11.1 meetings DDL). Nullable — the join path names it on new rows; a caller that
    # does not know it yet stores NULL rather than a fabricated value.
    row = await conn.fetchrow(
        """
        INSERT INTO meetings
            (tenant_id, repo_id, meeting_url, pinned_sha, recall_bot_id, status, platform)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, tenant_id, repo_id, pinned_sha, recall_bot_id, status, platform
        """,
        tenant_id,
        repo_id,
        meeting_url,
        pinned_sha,
        recall_bot_id,
        status,
        platform,
    )
    return dict(row)


async def mark_ended(conn: Any, *, meeting_id: Any) -> dict[str, Any] | None:
    """Flip a meeting to ``ended`` and stamp ``ended_at`` (the ordered-close, §3.16).

    Called from the ordered close when the meeting ends (status→'ended'). Idempotent:
    a meeting already ``ended`` keeps its FIRST ``ended_at`` (``COALESCE`` never
    overwrites a recorded end time), so a re-run of a completed close is a no-op on the
    timestamp. Returns the updated row, or ``None`` if no meeting matches.
    """
    row = await conn.fetchrow(
        """
        UPDATE meetings
           SET status = 'ended',
               ended_at = COALESCE(ended_at, now())
         WHERE id = $1
        RETURNING id, tenant_id, repo_id, status, ended_at
        """,
        meeting_id,
    )
    return dict(row) if row is not None else None


async def update_bot_id(
    conn: Any, *, meeting_id: Any, recall_bot_id: str
) -> dict[str, Any] | None:
    """Write the launched Recall bot_id back onto the meetings row (AC-JOIN-10).

    Called once the bot actually launches so the stored ``recall_bot_id`` equals
    the launched bot's id — the value a later Recall webhook carries (AC-JOIN-11).
    Returns the updated row, or ``None`` if no meeting matches ``meeting_id``.
    """
    row = await conn.fetchrow(
        """
        UPDATE meetings
           SET recall_bot_id = $2
         WHERE id = $1
        RETURNING id, tenant_id, repo_id, pinned_sha, recall_bot_id, status
        """,
        meeting_id,
        recall_bot_id,
    )
    return dict(row) if row is not None else None


async def get_by_bot_id(conn: Any, recall_bot_id: str) -> dict[str, Any] | None:
    # ``pinned_sha``/``recall_bot_id``/``meeting_url`` ride along (additively) so the
    # meeting-boot path can assemble the in-meeting engine off THIS one resolve —
    # the engine's map load is keyed on the meeting's exact pinned sha, never "latest".
    row = await conn.fetchrow(
        "SELECT id, tenant_id, repo_id, pinned_sha, recall_bot_id, meeting_url "
        "FROM meetings WHERE recall_bot_id = $1",
        recall_bot_id,
    )
    return dict(row) if row is not None else None


async def get_by_id(conn: Any, meeting_id: Any) -> dict[str, Any] | None:
    """Resolve a meeting row (``id``/``tenant_id``/``repo_id``) from its meeting id.

    The Workroom's per-task code-intel mount needs the meeting's ``repo_id``/``tenant_id`` to
    locate that repo's per-tenant ``graph.db`` index (§12.2) — but a Workroom task carries only
    the ``meeting_id`` (``bundle.notes_ref``), not the bot id. Returns ``None`` when no meeting
    matches (fail closed — the mount then degrades to no code_intel server)."""
    row = await conn.fetchrow(
        "SELECT id, tenant_id, repo_id FROM meetings WHERE id = $1",
        meeting_id,
    )
    return dict(row) if row is not None else None


async def upsert_repo_for_tenant(
    conn: Any,
    *,
    tenant_id: Any,
    full_name: str,
    default_branch: str | None = None,
    github_installation_id: str | None = None,
) -> dict[str, Any]:
    """Ensure a ``repos`` row exists for ``(tenant_id, full_name)`` and return it (idempotent).

    The connect→index flow writes ``tenants`` + ``connect_readiness`` + ``repo_maps`` but never a
    ``repos`` row, so ``POST /meetings`` (which resolves the invited repo via
    :func:`get_repo_for_tenant`) 404s even after a clean index. This is the missing insert on the
    connect SUCCESS path: it binds the tenant's repo durably so the invite route can find it.

    ``full_name`` is stored EXACTLY as the caller will name the repo in the ``POST /meetings``
    body (``get_repo_for_tenant`` matches it byte-for-byte), and it must derive — via
    ``premeeting.paths.repo_name_from_url`` — to the SAME key the map was stored under
    (``repo_maps.repo``), so the invite's HEAD-pin read finds the map. Passing the connect
    ``repo_url`` verbatim satisfies both at once.

    Idempotent: the ``repos`` table has no unique constraint on ``(tenant_id, full_name)``, so a
    re-run reads the existing row (returning it, optionally backfilling a NULL
    ``default_branch`` / ``github_installation_id``) rather than inserting a duplicate — a
    redelivered connect never double-writes the binding. Tenant-scoped by construction (the read
    and the insert both carry ``tenant_id``), so no cross-tenant row is ever created or returned.
    """
    existing = await conn.fetchrow(
        "SELECT id, tenant_id, full_name, default_branch, github_installation_id "
        "FROM repos WHERE tenant_id = $1 AND full_name = $2",
        tenant_id,
        full_name,
    )
    if existing is not None:
        # Backfill only NULLs we now know — never overwrite a recorded value with a fabricated one.
        if (default_branch and existing["default_branch"] is None) or (
            github_installation_id and existing["github_installation_id"] is None
        ):
            updated = await conn.fetchrow(
                """
                UPDATE repos
                   SET default_branch = COALESCE(default_branch, $3),
                       github_installation_id = COALESCE(github_installation_id, $4)
                 WHERE tenant_id = $1 AND full_name = $2
                RETURNING id, tenant_id, full_name, default_branch, github_installation_id
                """,
                tenant_id,
                full_name,
                default_branch,
                github_installation_id,
            )
            return dict(updated)
        return dict(existing)
    row = await conn.fetchrow(
        """
        INSERT INTO repos (tenant_id, full_name, default_branch, github_installation_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id, tenant_id, full_name, default_branch, github_installation_id
        """,
        tenant_id,
        full_name,
        default_branch,
        github_installation_id,
    )
    return dict(row)


async def get_repo_for_tenant(
    conn: Any, *, tenant_id: Any, full_name: str
) -> dict[str, Any] | None:
    """Resolve a repo row by its ``full_name`` WITHIN one tenant (fail closed).

    The invite route (``POST /meetings``) names the repo by ``full_name`` and must
    prove it belongs to the CALLER's tenant before any meeting binds to it. The
    query is tenant-filtered by construction (isolation triad, R-INV-09): a repo
    owned by another tenant resolves to ``None`` exactly like a repo that does not
    exist — the read itself cannot distinguish the two, so no existence leaks.
    """
    row = await conn.fetchrow(
        "SELECT id, tenant_id, full_name, default_branch FROM repos "
        " WHERE tenant_id = $1 AND full_name = $2",
        tenant_id,
        full_name,
    )
    return dict(row) if row is not None else None


async def get_repo_by_id(conn: Any, repo_id: Any) -> dict[str, Any] | None:
    """Resolve a repo row (``id``/``tenant_id``/``full_name``) from its id.

    The meeting-join path needs the repo's ``full_name`` to locate its per-tenant
    ``graph.db`` (Doc 01's index) so the Scribe starts with a real referent corpus
    (§3.4 code orientation). Returns ``None`` when no repo matches (fail closed).
    """
    row = await conn.fetchrow(
        "SELECT id, tenant_id, full_name, default_branch FROM repos WHERE id = $1",
        repo_id,
    )
    return dict(row) if row is not None else None
