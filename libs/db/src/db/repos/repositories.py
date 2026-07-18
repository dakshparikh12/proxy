"""Thin per-domain repository classes (§11 facade/repos pattern, no ORM).

Each repository borrows a connection from the :class:`~db.database.Database`
pool and delegates to the parameterised SQL functions in the sibling repo
modules. They are deliberately thin — the raw SQL stays the single source of
truth, matched to the canonical Alembic DDL.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import cost, drafts, meetings, sessions, transcript, webhooks

if TYPE_CHECKING:  # pragma: no cover
    from ..database import Database


class MeetingRepository:
    """The meeting bound to (tenant, repo, pinned_sha)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, **kwargs: Any) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            return await meetings.insert_meeting(conn, **kwargs)

    async def get_by_bot_id(self, recall_bot_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            return await meetings.get_by_bot_id(conn, recall_bot_id)


class TranscriptRepository:
    """Transcript segments; the comprehension flip is atomic with the note delta."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def flip_and_append(self, segment_id: Any, delta: str) -> None:
        async with self._db.acquire() as conn:
            async with conn.transaction():
                await transcript.flip_and_append(conn, segment_id, delta)


class NotesRepository:
    """Note deltas — persisted atomically with the transcript comprehension flip."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def apply_delta(self, segment_id: Any, delta: str) -> None:
        async with self._db.acquire() as conn:
            async with conn.transaction():
                await transcript.flip_and_append(conn, segment_id, delta)


class SandboxRepository:
    """Staged drafts + sandbox-derived artifacts (durable at creation)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def stage_draft(self, **kwargs: Any) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            return await drafts.insert_draft(conn, **kwargs)

    async def get_draft(self, draft_id: Any) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            return await drafts.get_draft(conn, draft_id)


class OperationRepository:
    """operation_runs — the one durable ops table (claim/heartbeat/reconcile)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, scope_id: str, operation_type: str) -> dict[str, Any] | None:
        return await self._db.get_operation_run(scope_id, operation_type)


class Repos:
    """The ``repos`` namespace of thin per-domain repositories the facade owns."""

    def __init__(self, db: Database) -> None:
        self.meetings = MeetingRepository(db)
        self.transcript = TranscriptRepository(db)
        self.notes = NotesRepository(db)
        self.sandbox = SandboxRepository(db)
        self.operations = OperationRepository(db)
        # The parameterised-SQL modules stay reachable for callers that own their
        # own transaction boundary.
        self.cost = cost
        self.sessions = sessions
        self.webhooks = webhooks
