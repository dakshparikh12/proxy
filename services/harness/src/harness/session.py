"""Sign-in + session resolution — a signed cookie over a server-side session.

Google-OAuth sign-in creates-or-loads a users row and mints a signed session
cookie; resolve_session verifies the signature and returns {user_id, tenant_id}.
An unsigned/tampered cookie does not resolve.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos


def _signing_key() -> bytes:
    # Env-provided in every real environment; a clearly-dev fallback keeps local
    # runs working. Never a production credential.
    return os.environ.get("SESSION_SIGNING_KEY", "dev-insecure-session-key").encode()


def _sign(session_id: str) -> str:
    mac = hmac.new(_signing_key(), session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{mac}"


def _verify(cookie: str) -> str | None:
    if "." not in cookie:
        return None
    session_id, _, mac = cookie.rpartition(".")
    expected = hmac.new(
        _signing_key(), session_id.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    return session_id


@dataclass(frozen=True)
class SignInResult:
    cookie: str
    user_id: Any
    tenant_id: Any


async def complete_signin(db: Database, *, email: str) -> SignInResult:
    """Complete OAuth sign-in: users row + signed session cookie."""
    async with db.acquire() as conn:
        user = await repos.identity.upsert_user_by_email(conn, email)
        session_id = await repos.sessions.create_session(
            conn, user["id"], user["tenant_id"]
        )
    return SignInResult(
        cookie=_sign(str(session_id)),
        user_id=user["id"],
        tenant_id=user["tenant_id"],
    )


async def resolve_session(
    db: Database, cookies: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve a signed session cookie → {user_id, tenant_id}, or None."""
    raw = cookies.get("session")
    if not raw:
        return None
    session_id = _verify(str(raw))
    if session_id is None:
        return None
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        return None
    async with db.acquire() as conn:
        row = await repos.sessions.get_session(conn, session_uuid)
    if row is None:
        return None
    return {"user_id": row["user_id"], "tenant_id": row["tenant_id"]}
