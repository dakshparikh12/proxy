"""The session-less ``/internal/notes`` handler — AC-TEN-004.

This handler is mounted OUTSIDE the session-auth wall, so it is gated by a shared
*internal* token rather than a session principal. Given a ``meeting_id`` it
resolves that meeting's OWNING tenant server-side (never trusting a
caller-supplied tenant) and folds ONLY that meeting's own-tenant ``note_deltas``.

It is fail-closed: a missing/invalid token, or a meeting that does not resolve to
an owning tenant, returns NO notes. That is what makes the session-less internal
path unable to leak another tenant's notes — the frontier the session-based
``/m/`` oracle structurally cannot exercise.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Protocol

# The shared internal token gating this path. Read from the environment; the
# default is the MVP/test token. A constant-time compare avoids a timing oracle.
_INTERNAL_TOKEN_ENV = "PROXY_INTERNAL_TOKEN"
_DEFAULT_INTERNAL_TOKEN = "internal-token-good"


def _expected_token() -> str:
    return os.environ.get(_INTERNAL_TOKEN_ENV, _DEFAULT_INTERNAL_TOKEN)


def _token_ok(token: Any) -> bool:
    """True only for a non-empty string equal to the configured internal token."""
    if not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(token, _expected_token())


class NotesStore(Protocol):
    """Server-side resolution of a meeting to its owning tenant and its notes."""

    def resolve_meeting_notes(
        self, meeting_id: str
    ) -> tuple[str, list[Any]] | None:
        """Return ``(owning_tenant_id, note_deltas)`` or ``None`` if unknown."""
        ...


class _PostgresNotesStore:
    """Fail-closed Postgres resolver.

    Returns ``None`` unless a DSN is configured AND the meeting exists; the notes
    are scoped to the meeting's OWN owning tenant, so a foreign tenant's notes are
    never reachable through this path.
    """

    def resolve_meeting_notes(
        self, meeting_id: str
    ) -> tuple[str, list[Any]] | None:
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            return None
        try:
            import psycopg
        except Exception:
            return None
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                owner = conn.execute(
                    "SELECT tenant_id FROM meetings WHERE id = %s",
                    (meeting_id,),
                ).fetchone()
                if owner is None or owner[0] is None:
                    return None
                tenant_id = str(owner[0])
                rows = conn.execute(
                    "SELECT id, op, body, created_at FROM note_deltas "
                    "WHERE meeting_id = %s AND tenant_id = %s "
                    "ORDER BY created_at",
                    (meeting_id, tenant_id),
                ).fetchall()
                notes: list[Any] = list(rows)
                return tenant_id, notes
        except Exception:
            return None


_DEFAULT_STORE: NotesStore = _PostgresNotesStore()


def get_notes(
    meeting_id: str,
    token: Any = None,
    *,
    store: NotesStore | None = None,
) -> dict[str, Any] | None:
    """Handle one ``/internal/notes`` read. Token-gated and meeting->tenant scoped.

    Returns ``None`` (no notes) on a missing/invalid token or an unresolvable
    meeting; otherwise ``{"meeting_id", "tenant_id", "notes"}`` scoped to the
    meeting's OWN owning tenant — never a foreign tenant's notes.
    """
    if not _token_ok(token):
        return None
    resolver = store if store is not None else _DEFAULT_STORE
    resolved = resolver.resolve_meeting_notes(meeting_id)
    if resolved is None:
        return None
    tenant_id, notes = resolved
    return {"meeting_id": meeting_id, "tenant_id": tenant_id, "notes": notes}


# Importable as ``libs.http.internal.get_notes`` and, via the facade re-export,
# as ``libs.http.internal_notes``.
internal_notes = get_notes
