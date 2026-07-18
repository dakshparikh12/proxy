"""libs.db.repos — parameterised SQL grouped by aggregate.

Each function takes a borrowed asyncpg connection (``db.acquire()``) so callers
own their transaction boundary. No ORM: raw SQL is the single source of truth,
matched to the canonical DDL in the Alembic migration.
"""
from __future__ import annotations

from . import cost as cost
from . import drafts as drafts
from . import identity as identity
from . import meetings as meetings
from . import sessions as sessions
from . import transcript as transcript
from . import webhooks as webhooks

__all__ = [
    "cost",
    "drafts",
    "identity",
    "meetings",
    "sessions",
    "transcript",
    "webhooks",
]
