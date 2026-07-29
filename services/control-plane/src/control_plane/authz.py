"""control_plane authorization — the cross-tenant read barrier (invariant 9).

Every read is scoped to the caller's authenticated tenant server-side. A principal
from tenant B reading a tenant-A meeting is refused and ZERO rows about tenant A
leak — the query itself is tenant-scoped, so a mismatched tenant simply returns no
row (a cross-tenant read is a P0 breach).
"""
from __future__ import annotations

from typing import Any


class CrossTenantReadDenied(Exception):
    """Raised when a principal reads across a tenant boundary (P0 breach)."""


class CsrfInvalid(Exception):
    """Raised when a state-changing request carries an invalid CSRF token."""


def read_meeting(conn: Any, *, meeting_id: Any, principal_tenant: Any) -> Any:
    """Read a meeting ONLY if it belongs to the principal's tenant, else deny."""
    row = conn.execute(
        "SELECT id, tenant_id, repo_id, pinned_sha, status "
        "FROM meetings WHERE id = %s AND tenant_id = %s",
        (meeting_id, principal_tenant),
    ).fetchone()
    if row is None:
        raise CrossTenantReadDenied(
            f"access denied: meeting {meeting_id!r} is not visible to this tenant"
        )
    return row


def draft_owner_tenant(conn: Any, *, draft_id: Any) -> Any:
    """Resolve a staged draft's owning tenant SERVER-SIDE (draft->meeting->tenant).

    The accept route NEVER trusts a client-supplied tenant (§12.9, invariant 9):
    the authoritative owner is derived by joining the persisted ``staged_drafts``
    row to its ``meetings`` row and reading ``meetings.tenant_id``. Returns the
    owning ``tenant_id`` (as ``str``) - or raises :class:`LookupError` when the
    draft does not exist (an accept for a non-existent draft is not a tenant match
    and must not silently succeed).
    """
    row = conn.execute(
        "SELECT m.tenant_id "
        "FROM staged_drafts d JOIN meetings m ON m.id = d.meeting_id "
        "WHERE d.draft_id = %s",
        (draft_id,),
    ).fetchone()
    if row is None or row[0] is None:
        raise LookupError(f"no owning tenant for draft {draft_id!r}")
    return str(row[0])


def authorize_draft_accept(
    conn: Any, *, draft_id: Any, principal_tenant: Any, csrf_valid: bool
) -> None:
    """Fail-closed gate for a draft accept: CSRF first, then server-side tenant.

    Raises :class:`CsrfInvalid` on a bad CSRF token and
    :class:`CrossTenantReadDenied` when the principal's authenticated tenant does
    not own the draft (resolved server-side via :func:`draft_owner_tenant` - a
    client-supplied tenant is structurally never consulted). Returns ``None`` when
    the accept is authorized. Raising (never returning a bool) keeps the caller's
    world-touching apply strictly downstream of the barrier.
    """
    if not csrf_valid:
        raise CsrfInvalid("invalid CSRF token")
    owner = draft_owner_tenant(conn, draft_id=draft_id)
    if str(principal_tenant) != owner:
        raise CrossTenantReadDenied(
            f"access denied: draft {draft_id!r} is not visible to this tenant"
        )
