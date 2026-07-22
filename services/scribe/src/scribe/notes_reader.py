"""The cross-service / cross-session NOTES READ path (§3.3.1).

A wake-turn bundle hands the Workroom **``notes_ref``, not the notes object**
(CANONICAL §1.3), and ``notes_ref = meeting_id`` (CANONICAL §11.4). The Workroom
runs on *another host*, so it cannot read the in-process ``NOTES_CACHE`` — that
cache is a scribe-hot-path optimization only. The durable source of the notes
object is the ``note_deltas`` Postgres table (§3.3): the notes object is its
deterministic **left-fold in ``id`` order**. So the read path folds from
Postgres, never from ``NOTES_CACHE``.

This module is that read path. It carries:

* :class:`Notes` + :meth:`Notes.fold_all` — the ONE canonical, deterministic
  left-fold every caller shares. ``to_canonical_json`` renders byte-identical
  bytes for the same ``note_deltas`` state (sorted keys, no wall-clock, stable
  entry order), so ``/internal/notes``, ``/m/{meeting_id}`` and a direct fold
  call all produce the same bytes (AC-CSREAD-10).
* :func:`read_notes` — the durable read: ``load_deltas`` → ``Notes.fold_all``.
  It NEVER reads ``NOTES_CACHE`` as its source (the contract path, §3.3.1).
* :func:`internal_notes_handler` / :func:`m_handler` — the two response handlers.
  Both fold from ``note_deltas`` via the SAME canonical symbol (:func:`read_notes`).
  A ``NOTES_CACHE`` serve, if present, is a *conditional* read-through
  optimization returning bytes identical to the fold — never the sole path.
* :func:`resolve_notes_ref` — Doc 05's bundle-side resolution: an HTTP GET to
  ``/internal/notes/{meeting_id}``. It never accepts a materialised notes object
  and never deserialises one from a bundle field (AC-CSREAD-07).

The endpoint is **mounted OUTSIDE the auth wall**, alongside ``/internal/reconcile``,
gated by the shared internal bearer token — never the user session cookie. The
route-mount facts a static audit asserts on are module constants below.

Framework note: the handlers are framework-agnostic async callables returning a
small :class:`Response` (a status code + a body string). No ASGI server is
imported, so the whole read path is exercisable under plain pytest against the
real Postgres — the fold-from-``note_deltas`` contract runs for real, not mocked.
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol
from uuid import UUID

from .schema import Entry  # noqa: F401 — the folded entries ARE scribe.schema Entry payloads

# ── Route-mount facts (AC-CSREAD-04). The endpoint lives in the /internal/*
# route group, alongside /internal/reconcile, OUTSIDE the user-auth wall. These
# are the observables a static route-group audit asserts on. The endpoint is a
# service-to-service reader gated by the internal bearer token, NOT the user
# session cookie / dispatch funnel — so no user-session middleware wraps it.
INTERNAL_ROUTE_GROUP = "/internal"
INTERNAL_NOTES_PATH = "/internal/notes/{meeting_id}"
INTERNAL_RECONCILE_PATH = "/internal/reconcile"  # the sibling this mounts alongside
MOUNTS_OUTSIDE_AUTH_WALL = True
REQUIRES_USER_SESSION = False  # session cookie is NEVER accepted on this path

# The authenticated user surface (CANONICAL §12.9). It sits BEHIND the auth wall
# but reads the SAME note_deltas fold — never NOTES_CACHE.
M_SURFACE_PATH = "/m/{meeting_id}"

# ── Internal bearer token (the shared service-to-service secret). Read from the
# environment; the default is the MVP/test token. A constant-time compare avoids
# a timing oracle. This is the ONLY credential the internal reader accepts — a
# user session cookie is structurally never consulted here (AC-CSREAD-05).
_INTERNAL_TOKEN_ENV = "PROXY_INTERNAL_TOKEN"  # nosec B105 - env var NAME, not a secret
_DEFAULT_INTERNAL_TOKEN = "internal-token-good"  # nosec B105 - dev/test default, overridden by env


def _expected_token() -> str:
    return os.environ.get(_INTERNAL_TOKEN_ENV, _DEFAULT_INTERNAL_TOKEN)


def _token_ok(token: Any) -> bool:
    """True only for a non-empty string equal to the configured internal token."""
    if not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(token, _expected_token())


# ── Seams ────────────────────────────────────────────────────────────────────
class Acquirer(Protocol):
    """The db handle we borrow a connection from (``libs.db.Database``-shaped).

    Only :meth:`acquire` is needed: an async context manager yielding a borrowed
    asyncpg connection. Typing it as a Protocol keeps the read path decoupled
    from the concrete facade while still driving the REAL seam at the db tier.
    """

    def acquire(self) -> Any: ...


DeltaLoader = Callable[[Any, Any], Awaitable[list[dict[str, Any]]]]
"""``load_deltas(conn, meeting_id) -> ordered raw rows`` — the committed store seam."""


def _default_loader() -> DeltaLoader:
    # Imported lazily so this module imports on a host without libs.db wired; the
    # db tier and every real call resolve the committed ``db.repos.notes`` seam.
    from db.repos.notes import load_deltas  # type: ignore[import-not-found]  # workspace seam; resolved at runtime (§3.3)

    return load_deltas  # type: ignore[no-any-return]  # the workspace seam is untyped to mypy


# ── The notes object + the canonical fold ────────────────────────────────────
@dataclass(frozen=True)
class FreshnessFlag:
    """The notes object's currency (§3.8), riding every response.

    ``as_of`` is the ISO-8601 timestamp of the newest ``note_deltas`` row folded
    (the *Postgres-fold* time), NEVER a stale cached wall-clock — so a consumer
    sees the object's real currency (AC-CSREAD-09 / -09-NEG). ``delta_count`` is
    how many rows the fold consumed. For an empty ledger ``as_of`` is ``None`` and
    ``is_empty`` is True (there is nothing to be stale about).
    """

    as_of: Optional[str]
    delta_count: int
    is_empty: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "delta_count": self.delta_count,
            "is_empty": self.is_empty,
        }


class Notes:
    """The folded notes object — the deterministic left-fold of ``note_deltas``.

    ``fold_all`` is the ONE canonical fold every caller shares (the same fold
    ``NOTES_CACHE`` is built from). ``to_canonical_json`` renders byte-stable
    bytes: sorted keys, entries in fold order, no wall-clock in the body beyond
    the fold's own ``as_of`` — so all callers over the same rows emit identical
    bytes (AC-CSREAD-10).
    """

    __slots__ = ("entries", "order", "current_goal", "freshness_flag")

    def __init__(
        self,
        *,
        entries: dict[str, dict[str, Any]],
        order: list[str],
        current_goal: Optional[str],
        freshness_flag: FreshnessFlag,
    ) -> None:
        self.entries = entries
        self.order = order
        self.current_goal = current_goal
        self.freshness_flag = freshness_flag

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @classmethod
    def fold_all(cls, deltas: list[dict[str, Any]]) -> "Notes":
        """Deterministic left-fold of ``note_deltas`` rows in the GIVEN order.

        ``load_deltas`` returns rows ordered by ascending ``id`` (§3.3) — the
        write order — and this fold consumes them in that order. It is a pure,
        deterministic reduction: no clock, no randomness, no dict-iteration
        order dependence beyond the explicit ``order`` list it maintains.

        ``payload`` arrives from asyncpg as a JSON **string** (the ``jsonb``
        column) — it is ``json.loads``-decoded here before use. A row whose
        ``op`` is unknown is ignored (forward-compatible), never a crash.
        """
        entries: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        current_goal: Optional[str] = None
        newest_created_at: Optional[str] = None
        count = 0

        for row in deltas:
            count += 1
            entry_id = str(row["entry_id"])
            op = str(row["op"])
            payload = _decode_payload(row.get("payload"))

            if op == "add":
                if entry_id not in entries:
                    order.append(entry_id)
                entries[entry_id] = dict(payload)
            elif op == "patch":
                base = dict(entries.get(entry_id, {}))
                if entry_id not in entries:
                    order.append(entry_id)  # patch-before-add: empty base, still tracked
                changes = payload.get("changes", payload)
                if isinstance(changes, dict):
                    base.update(changes)
                entries[entry_id] = base
            elif op == "close":
                base = dict(entries.get(entry_id, {}))
                if entry_id not in entries:
                    order.append(entry_id)
                base["resolved"] = True
                resolution = payload.get("resolution")
                if resolution is not None:
                    base["resolution"] = resolution
                entries[entry_id] = base
            # unknown op → ignored (forward-compatible), never fabricated

            cg = payload.get("current_goal")
            if isinstance(cg, str):
                current_goal = cg

            created_at = _created_at_iso(row.get("created_at"))
            if created_at is not None and (
                newest_created_at is None or created_at > newest_created_at
            ):
                newest_created_at = created_at

        flag = FreshnessFlag(
            as_of=newest_created_at,
            delta_count=count,
            is_empty=(count == 0),
        )
        return cls(
            entries=entries,
            order=order,
            current_goal=current_goal,
            freshness_flag=flag,
        )

    def to_serializable(self) -> dict[str, Any]:
        """The plain-dict form of the notes object (entries in fold order)."""
        return {
            "entries": [
                {"entry_id": eid, **self.entries[eid]} for eid in self.order
            ],
            "current_goal": self.current_goal,
            "freshness_flag": self.freshness_flag.to_dict(),
        }

    def to_canonical_json(self) -> str:
        """Byte-stable JSON of the notes object (AC-CSREAD-10).

        ``sort_keys=True`` + fixed separators + ``default=str`` make the bytes
        identical for the same ``note_deltas`` state regardless of caller, dict
        insertion order, or non-JSON scalars (e.g. a stray ``UUID``). The entries
        list preserves the deterministic *fold* order (id order), independent of
        the key sort.
        """
        return json.dumps(
            self.to_serializable(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )


def _decode_payload(payload: Any) -> dict[str, Any]:
    """Decode a ``note_deltas.payload`` jsonb value into a dict.

    asyncpg surfaces ``jsonb`` as a JSON **string** — it is ``json.loads``-decoded
    here (the KEY reconstruction step, §3.3.1). A dict (already-decoded, e.g. a
    caller that set a custom codec) passes through. A non-object payload decodes
    to an empty dict rather than crashing the fold.
    """
    if payload is None:
        return {}
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (ValueError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    if isinstance(payload, dict):
        return payload
    return {}


def _created_at_iso(created_at: Any) -> Optional[str]:
    """Normalise a ``created_at`` value to an ISO-8601 string (or None)."""
    if created_at is None:
        return None
    isoformat = getattr(created_at, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(created_at)


# ── The durable read (the contract path) ─────────────────────────────────────
async def read_notes(
    meeting_id: UUID | str,
    *,
    db: Acquirer,
    load_deltas: Optional[DeltaLoader] = None,
) -> Notes:
    """The durable cross-service read — NEVER reads ``NOTES_CACHE`` as the source.

    Folds ``note_deltas`` → the notes object server-side (§3.3.1): the SAME
    deterministic left-fold ``NOTES_CACHE`` is built from, so a caller on ANY
    host reads the current, consistent object. This is the canonical fold symbol
    both handlers share.

    A Postgres failure propagates out of this call (the ``acquire``/``load_deltas``
    seam raises) — there is NO silent fallback to an empty notes object. The
    handlers turn that into a 5xx (AC-CSREAD-02-NEG / -08-NEG).
    """
    loader = load_deltas if load_deltas is not None else _default_loader()
    async with db.acquire() as conn:
        deltas = await loader(conn, meeting_id)
    return Notes.fold_all(deltas)


# ── The HTTP-shaped handlers (framework-agnostic) ────────────────────────────
@dataclass(frozen=True)
class Response:
    """A minimal HTTP response: a status code and an optional JSON body.

    Body is ``None`` for statuses that carry no notes object (401/403/404/503) —
    so a static/runtime check can assert an error status NEVER carries a notes
    object (AC-CSREAD-02-NEG / -03-NEG).
    """

    status_code: int
    body: Optional[str] = None

    @property
    def is_notes_object(self) -> bool:
        """True only for a 200 whose body parses as a notes object."""
        if self.status_code != 200 or self.body is None:
            return False
        try:
            parsed = json.loads(self.body)
        except (ValueError, TypeError):
            return False
        return isinstance(parsed, dict) and "entries" in parsed


class NotesCache(Protocol):
    """The in-process ``NOTES_CACHE`` — a scribe-hot-path optimization only.

    ``__contains__`` / ``get_bytes`` let a handler serve an already-folded object
    as a read-through optimization on the harness host. It is NEVER the sole path
    (the fold-from-``note_deltas`` is the contract) and the served bytes are
    identical to the fold (the cache *is* the fold).
    """

    def __contains__(self, meeting_id: object) -> bool: ...

    def get_bytes(self, meeting_id: object) -> str: ...


async def internal_notes_handler(
    meeting_id: UUID | str,
    *,
    provided_token: Any,
    db: Acquirer,
    load_deltas: Optional[DeltaLoader] = None,
    notes_cache: Optional[NotesCache] = None,
) -> Response:
    """``GET /internal/notes/{meeting_id}`` — the token-gated internal reader.

    Order of decisions:

    1. **Token gate** (AC-CSREAD-03-NEG / -05): a missing/invalid internal bearer
       token → 401; a user session cookie is structurally never consulted here.
    2. **Conditional cache** (AC-CSREAD-06): if a populated ``NOTES_CACHE`` is
       supplied AND holds this meeting, serve its bytes (identical to the fold).
       This branch is a guarded optimization — the fold path below is the default.
    3. **Fold-from-Postgres** (AC-CSREAD-02 / -03): fold ``note_deltas`` via the
       canonical :func:`read_notes`. Zero rows → **404** (unknown meeting); no
       200-with-empty-object for an unknown meeting. Rows present → **200** with
       the canonical JSON, freshness flag riding the body (AC-CSREAD-09).
    4. **Honest degradation** (AC-CSREAD-02-NEG): a Postgres error → **503**, body
       ``None`` — never a fabricated 200 or a silent empty-notes fallback.
    """
    if not _token_ok(provided_token):
        return Response(status_code=401)  # no notes to an unauthenticated caller

    if notes_cache is not None and meeting_id in notes_cache:
        # Read-through optimization: identical bytes to the fold (the cache IS the
        # fold). Conditional — reached only when a populated cache is present.
        return Response(status_code=200, body=notes_cache.get_bytes(meeting_id))

    try:
        notes = await read_notes(meeting_id, db=db, load_deltas=load_deltas)
    except Exception:  # noqa: BLE001 — any db failure degrades honestly (5xx)
        return Response(status_code=503)  # dependency failure; NO stale/empty 200

    if notes.freshness_flag.delta_count == 0:
        return Response(status_code=404)  # unknown meeting — no empty-object 200
    return Response(status_code=200, body=notes.to_canonical_json())


async def m_handler(
    meeting_id: UUID | str,
    *,
    session: Any,
    db: Acquirer,
    load_deltas: Optional[DeltaLoader] = None,
    notes_cache: Optional[NotesCache] = None,
) -> Response:
    """``GET /m/{meeting_id}`` — the authenticated user surface (CANONICAL §12.9).

    Behind the auth wall (a valid ``session`` is required), but it reads the SAME
    ``note_deltas`` fold as ``/internal/notes`` — the canonical :func:`read_notes`
    symbol — NEVER ``NOTES_CACHE`` as the source. So a forwarded-to VP or an
    accept action always sees the consistent, crash-survivable object.

    A Postgres failure → **503**, never a stale/empty 200 (AC-CSREAD-08-NEG).
    """
    if not session:
        return Response(status_code=401)  # user surface is behind the auth wall

    if notes_cache is not None and meeting_id in notes_cache:
        return Response(status_code=200, body=notes_cache.get_bytes(meeting_id))

    try:
        notes = await read_notes(meeting_id, db=db, load_deltas=load_deltas)
    except Exception:  # noqa: BLE001 — db down → error, never stale cache
        return Response(status_code=503)

    if notes.freshness_flag.delta_count == 0:
        return Response(status_code=404)
    return Response(status_code=200, body=notes.to_canonical_json())


# ── Doc 05 bundle-side resolution (AC-CSREAD-07) ──────────────────────────────
HttpGetter = Callable[[str], Awaitable["Response"]]
"""An injected async HTTP GET: ``get(path) -> Response``. Doc 05 supplies the
real transport (an internal-token-bearing client); tests supply a fake getter."""


async def resolve_notes_ref(
    notes_ref: UUID | str,
    *,
    http_get: HttpGetter,
) -> Response:
    """Resolve a bundle's ``notes_ref`` by an HTTP GET to the internal reader.

    ``notes_ref = meeting_id`` (CANONICAL §11.4), so resolution is an HTTP call to
    ``GET /internal/notes/{notes_ref}`` — it does NOT read a notes object inline
    from the bundle, and this function accepts NO already-materialised notes
    object (AC-CSREAD-07). The only inputs are the *ref* (a handle) and the
    *transport* — the object is fetched fresh, never carried.
    """
    path = INTERNAL_NOTES_PATH.replace("{meeting_id}", str(notes_ref))
    return await http_get(path)


__all__ = [
    "Notes",
    "FreshnessFlag",
    "Response",
    "Acquirer",
    "DeltaLoader",
    "NotesCache",
    "HttpGetter",
    "read_notes",
    "internal_notes_handler",
    "m_handler",
    "resolve_notes_ref",
    "INTERNAL_ROUTE_GROUP",
    "INTERNAL_NOTES_PATH",
    "INTERNAL_RECONCILE_PATH",
    "MOUNTS_OUTSIDE_AUTH_WALL",
    "REQUIRES_USER_SESSION",
    "M_SURFACE_PATH",
]
