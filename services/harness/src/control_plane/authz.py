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
