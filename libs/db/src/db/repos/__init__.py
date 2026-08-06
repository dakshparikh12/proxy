"""libs.db.repos — parameterised SQL grouped by aggregate.

Each function takes a borrowed asyncpg connection (``db.acquire()``) so callers
own their transaction boundary. No ORM: raw SQL is the single source of truth,
matched to the canonical DDL in the Alembic migration.
"""
from __future__ import annotations

from . import connect as connect
from . import drafts as drafts
from . import identity as identity
from . import meetings as meetings
from . import sessions as sessions
from . import webhooks as webhooks

__all__ = [
    "connect",
    "drafts",
    "identity",
    "meetings",
    "sessions",
    "webhooks",
]
