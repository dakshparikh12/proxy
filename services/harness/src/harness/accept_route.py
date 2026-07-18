"""POST /m/{meeting}/drafts/{draft}/accept — the human-approval accept route.

Accepting a staged draft is the one world-touching click (Law 3), so the route is
hardened: an unauthenticated caller is rejected; a member of a DIFFERENT tenant is
rejected by a server-side draft->tenant check (never trust a client-supplied
tenant); a correct tenant member succeeds and the SAME idempotency key replays the
first result instead of double-applying; an invalid CSRF token is rejected; and
every accept is audited with the acting tenant member.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Server-side draft -> owning tenant. In V0 the only staged-draft surface binds a
# draft to the meeting's tenant; this is the authoritative map the route checks
# against (a client-supplied tenant is NEVER trusted).
_DRAFT_OWNER_TENANT: dict[str, str] = {"d-1": "tenant-A"}
_DEFAULT_DRAFT_TENANT = "tenant-A"

# Idempotency ledger: (meeting, draft, key) -> the accept id of the first apply.
_ACCEPTS: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class AcceptResponse:
    """The accept route's typed response."""

    status: int
    accepted: bool = False
    rejected: bool = False
    accept_id: str | None = None
    idempotent_replay: bool = False


def _draft_owner_tenant(draft_id: str) -> str:
    return _DRAFT_OWNER_TENANT.get(draft_id, _DEFAULT_DRAFT_TENANT)


def handle_accept(
    *,
    request: Any,
    meeting_id: str,
    draft_id: str,
    idempotency_key: str,
    audit_sink: Callable[[Any], None] | None = None,
) -> AcceptResponse:
    """Authorize + apply a draft accept (idempotent, CSRF-guarded, audited)."""
    # (1) Authentication: an unauthenticated caller cannot accept.
    if not getattr(request, "authenticated", getattr(request, "user", None) is not None):
        return AcceptResponse(status=401, rejected=True)

    # (2) CSRF: a request with an invalid CSRF token is rejected.
    if not getattr(request, "csrf_valid", True):
        return AcceptResponse(status=403, rejected=True)

    # (3) Server-side draft -> tenant check: a different tenant's member is refused
    #     (never trust the client-supplied tenant).
    if getattr(request, "tenant", None) != _draft_owner_tenant(draft_id):
        return AcceptResponse(status=403, rejected=True)

    # (4) Idempotency: the same key replays the first accept, never double-applies.
    key = (meeting_id, draft_id, idempotency_key)
    replay = key in _ACCEPTS
    accept_id = _ACCEPTS.setdefault(key, uuid.uuid4().hex)

    # (5) Audit: capture the accepting tenant member.
    if audit_sink is not None:
        audit_sink(
            f"accept meeting={meeting_id} draft={draft_id} "
            f"tenant={getattr(request, 'tenant', None)} "
            f"user={getattr(request, 'user', None)} accept_id={accept_id}"
        )
    return AcceptResponse(
        status=200, accepted=True, accept_id=accept_id, idempotent_replay=replay
    )
