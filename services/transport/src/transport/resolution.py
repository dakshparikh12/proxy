"""Recall webhook ``bot_id`` → meeting → (tenant, repo), fail-closed (AC-JOIN-11).

Every Recall webhook carries a ``bot_id``. It must resolve to exactly one meetings
row and its tenant + repo (the meetings row + ``recall_bot_id`` write-back is doc00's
``libs/db`` — reused, never re-tabled). An **unknown** ``bot_id`` fails closed: no
default resolution, no cross-tenant fallback — the isolation triad (Invariant 9) holds
at the transport boundary. This wraps the injected repo so the fail-closed rule is a
single, testable seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class MeetingRef:
    """The tenant/repo a resolved bot_id belongs to."""

    meeting_id: Any
    tenant_id: Any
    repo_id: Any


class UnknownBotError(LookupError):
    """A webhook bot_id resolved to no meeting — fail closed, never a default."""


class MeetingResolver(Protocol):
    """Injected read side of ``libs/db`` meetings (``get_by_bot_id``)."""

    async def get_by_bot_id(self, recall_bot_id: str) -> dict[str, Any] | None: ...


async def resolve_meeting(resolver: MeetingResolver, recall_bot_id: str) -> MeetingRef:
    """Resolve a webhook ``bot_id`` to its meeting; raise if unknown (fail closed)."""
    row = await resolver.get_by_bot_id(recall_bot_id)
    if row is None:
        raise UnknownBotError(f"unknown recall_bot_id {recall_bot_id!r} — fail closed, no default resolution")
    return MeetingRef(
        meeting_id=row["id"],
        tenant_id=row["tenant_id"],
        repo_id=row.get("repo_id"),
    )
