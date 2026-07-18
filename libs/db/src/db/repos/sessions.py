"""sessions repository — server-side session records behind a signed cookie."""
from __future__ import annotations

from typing import Any


async def create_session(conn: Any, user_id: Any, tenant_id: Any) -> Any:
    return await conn.fetchval(
        "INSERT INTO sessions (user_id, tenant_id) VALUES ($1, $2) RETURNING id",
        user_id,
        tenant_id,
    )


async def get_session(conn: Any, session_id: Any) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT id, user_id, tenant_id FROM sessions WHERE id = $1",
        session_id,
    )
    return dict(row) if row is not None else None
