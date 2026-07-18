"""Synchronous facade over a raw psycopg3 connection.

The async :class:`~db.database.Database` owns the production asyncpg pool; this
module is a thin, connection-scoped SYNC mirror used by the broker-free workflow
path (a caller that already holds one autocommit psycopg3 connection and wants
the connect -> bind -> meeting chain without an event loop).

It never opens or closes the connection — the caller owns that lifetime. The raw
parameterised SQL here mirrors the async repos and the canonical Alembic DDL
(``migrations/versions/0001_substrate.py``): Postgres remains the source of truth.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SignInResult:
    """A signed-in session; ``cookie`` is the opaque session id (there is no
    separate cookie column — the session's uuid IS the cookie)."""

    cookie: str
    user_id: str
    tenant_id: str


@dataclass(frozen=True)
class RepoRow:
    """A repos row bound to a tenant (GitHub App install target)."""

    id: Any
    tenant_id: Any
    full_name: str | None
    default_branch: str | None


@dataclass(frozen=True)
class MeetingRow:
    """A meetings row bound to (tenant, repo, pinned_sha), Recall bot launched."""

    id: Any
    tenant_id: Any
    repo_id: Any
    pinned_sha: str | None
    recall_bot_id: str | None


class _SyncSessions:
    """Sign-in: create-or-load the tenant + user, then mint a session row."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def sign_in(self, *, email: str) -> SignInResult:
        # upsert-user-by-email semantics (mirrors repos.identity): a new user is
        # bound to a fresh tenant so every downstream row reaches a tenant.
        existing = self._conn.execute(
            "SELECT id, tenant_id FROM users WHERE email = %s", (email,)
        ).fetchone()
        if existing is not None and existing[1] is not None:
            user_id, tenant_id = existing[0], existing[1]
        else:
            tenant_id = self._conn.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (email,)
            ).fetchone()[0]
            user_id = self._conn.execute(
                """
                INSERT INTO users (email, tenant_id)
                VALUES (%s, %s)
                ON CONFLICT (email) DO UPDATE
                    SET tenant_id = COALESCE(users.tenant_id, EXCLUDED.tenant_id)
                RETURNING id
                """,
                (email, tenant_id),
            ).fetchone()[0]

        session_id = self._conn.execute(
            "INSERT INTO sessions (user_id, tenant_id) VALUES (%s, %s) RETURNING id",
            (user_id, tenant_id),
        ).fetchone()[0]
        return SignInResult(
            cookie=str(session_id), user_id=str(user_id), tenant_id=str(tenant_id)
        )


class _SyncRepositories:
    """repos install: a repos row bound to the tenant."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create(
        self, *, tenant_id: Any, full_name: str, default_branch: str
    ) -> RepoRow:
        row = self._conn.execute(
            """
            INSERT INTO repos (tenant_id, full_name, default_branch)
            VALUES (%s, %s, %s)
            RETURNING id, tenant_id, full_name, default_branch
            """,
            (tenant_id, full_name, default_branch),
        ).fetchone()
        return RepoRow(
            id=row[0], tenant_id=row[1], full_name=row[2], default_branch=row[3]
        )


class _SyncMeetings:
    """Invite -> a meetings row bound to (tenant, repo, pinned_sha), bot launched."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create_from_invite(
        self, *, tenant_id: Any, repo_id: Any, pinned_sha: str
    ) -> MeetingRow:
        # simulate the Recall bot launch: a non-empty bot_id is written back so a
        # later webhook can resolve bot_id -> meeting -> (tenant, repo).
        recall_bot_id = f"bot-{uuid.uuid4().hex}"
        row = self._conn.execute(
            """
            INSERT INTO meetings
                (tenant_id, repo_id, pinned_sha, recall_bot_id, status)
            VALUES (%s, %s, %s, %s, 'live')
            RETURNING id, tenant_id, repo_id, pinned_sha, recall_bot_id
            """,
            (tenant_id, repo_id, pinned_sha, recall_bot_id),
        ).fetchone()
        return MeetingRow(
            id=row[0],
            tenant_id=row[1],
            repo_id=row[2],
            pinned_sha=row[3],
            recall_bot_id=row[4],
        )

    def resolve_bot(self, recall_bot_id: str) -> MeetingRow:
        row = self._conn.execute(
            """
            SELECT id, tenant_id, repo_id, pinned_sha, recall_bot_id
              FROM meetings
             WHERE recall_bot_id = %s
            """,
            (recall_bot_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"no meeting for recall_bot_id {recall_bot_id!r}")
        return MeetingRow(
            id=row[0],
            tenant_id=row[1],
            repo_id=row[2],
            pinned_sha=row[3],
            recall_bot_id=row[4],
        )


class _SyncRepos:
    """The connection-scoped ``repos`` namespace of the sync facade."""

    def __init__(self, conn: Any) -> None:
        self.sessions = _SyncSessions(conn)
        self.repos = _SyncRepositories(conn)
        self.meetings = _SyncMeetings(conn)


class _SyncDatabase:
    """A connection-scoped synchronous handle to the durable Postgres substrate.

    A lightweight mirror of :class:`~db.database.Database` for callers that hold
    one raw psycopg3 (autocommit) connection and want the sync repo namespace.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.repos = _SyncRepos(conn)

    def resolve_session(self, cookie: str) -> dict[str, Any]:
        """Round-trip a session cookie back to ``{user_id, tenant_id}``."""
        row = self._conn.execute(
            "SELECT user_id, tenant_id FROM sessions WHERE id = %s", (cookie,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no session for cookie {cookie!r}")
        return {"user_id": row[0], "tenant_id": row[1]}
