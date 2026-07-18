"""Identity/tenancy repository — tenants + users (A-009 tenant reachability).

Sign-in creates-or-loads a users row keyed by email; each new user is bound to a
tenant so every downstream row reaches a tenant.
"""
from __future__ import annotations

from typing import Any


async def upsert_user_by_email(conn: Any, email: str) -> dict[str, Any]:
    """Create-or-load the user for ``email``; ensure a bound tenant."""
    existing = await conn.fetchrow(
        "SELECT id, tenant_id FROM users WHERE email = $1", email
    )
    if existing is not None and existing["tenant_id"] is not None:
        return dict(existing)

    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id", email
    )
    row = await conn.fetchrow(
        """
        INSERT INTO users (email, tenant_id)
        VALUES ($1, $2)
        ON CONFLICT (email) DO UPDATE
            SET tenant_id = COALESCE(users.tenant_id, EXCLUDED.tenant_id)
        RETURNING id, tenant_id
        """,
        email,
        tenant_id,
    )
    return dict(row)
