"""connect_readiness repository — the durable readiness row the connect poll reads (§2.7).

This module carries the *parameterised SQL* for the connect page's readiness plane: the
one ``connect_readiness`` row per install that the background connect→index trigger WRITES
as the indexing pipeline progresses and that ``GET /connect/status`` READS. Postgres is the
source of truth (CLAUDE.md §"Source of truth vs cache") because ``control_plane`` is an
autoscaling multi-instance Cloud Run service — a poll can hit a different instance than the
one running the trigger, so an in-process dict would strand readiness at 'connecting' and
lose it on recycle (the exact failure the ``db:postgres`` dependency_class was written to
prevent).

Raw psycopg3 only — **no ORM** — matching the sibling sync facade (``db.sync``). Each
function takes a borrowed *autocommit* psycopg connection so the caller owns the
connection's lifetime; the SQL is matched byte-for-byte to the canonical DDL in
``migrations/versions/0006_connect_readiness.py``. ``status`` only ever holds a value from
the canonical Readiness enum (CANONICAL §1.5) — the CHECK constraint + the client-side
guard here make a 'mapping' state unrepresentable.
"""
from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Json

# The canonical Readiness enum (CANONICAL §1.5). ``mapping`` is deliberately absent — both
# this guard and the connect_readiness CHECK constraint reject any value outside this set,
# so a 'mapping' state can never land in the durable row.
_VALID_STATES: frozenset[str] = frozenset(
    {"connecting", "cloning", "indexing", "ready", "not_ready"}
)


def insert_install(
    conn: Any, *, install_id: str, tenant_id: str, repo_url: str
) -> None:
    """Insert a fresh connect install at state ``connecting`` (the initial durable row).

    Idempotent on the ``install_id`` PK — a re-insert of the same opaque handle is a silent
    no-op (a redelivered install/start never clobbers live progress).
    """
    conn.execute(
        """
        INSERT INTO connect_readiness (install_id, tenant_id, repo_url)
        VALUES (%s, %s, %s)
        ON CONFLICT (install_id) DO NOTHING
        """,
        (install_id, tenant_id, repo_url),
    )


def mark_state(conn: Any, *, install_id: str, state: str) -> None:
    """Advance an install to a canonical progress state, appending it to the ordered trail.

    Rejects any value outside the canonical Readiness enum client-side (a 'mapping' state or
    any typo raises ``ValueError``) BEFORE touching Postgres — belt-and-suspenders with the
    row's CHECK constraint. An unknown ``install_id`` is a no-op UPDATE (0 rows), surfaced by
    the caller as a missing install; it never silently invents a row.
    """
    if state not in _VALID_STATES:
        raise ValueError(
            f"{state!r} is not a canonical Readiness state ({sorted(_VALID_STATES)})"
        )
    # Append the state to ``states`` only when it is not already the tail, so the ordered
    # progression trail (connecting→cloning→indexing→…) has no consecutive duplicates.
    conn.execute(
        """
        UPDATE connect_readiness
           SET status = %s,
               states = CASE
                   WHEN states->>-1 = %s THEN states
                   ELSE states || to_jsonb(%s::text)
               END,
               updated_at = now()
         WHERE install_id = %s
        """,
        (state, state, state, install_id),
    )


def set_ready(
    conn: Any,
    *,
    install_id: str,
    coverage_pct: float,
    flagged: list[tuple[str, str]] | None = None,
) -> None:
    """Terminal ``ready``: the REAL coverage number + the flagged files (honest, §2.7).

    ``coverage_pct`` is the pipeline's genuine indexed/(indexed+flagged) fraction — never a
    literal constant. ``flagged`` is stored as a jsonb array of ``{path, reason}`` so the
    happy-path 'N files flagged: <reason>' detail survives an instance recycle.
    """
    flagged_json = [{"path": p, "reason": r} for (p, r) in (flagged or [])]
    conn.execute(
        """
        UPDATE connect_readiness
           SET status = 'ready',
               coverage_pct = %s,
               flagged = %s,
               gaps = '[]'::jsonb,
               states = CASE
                   WHEN states->>-1 = 'ready' THEN states
                   ELSE states || to_jsonb('ready'::text)
               END,
               updated_at = now()
         WHERE install_id = %s
        """,
        (float(coverage_pct), Json(flagged_json), install_id),
    )


def set_not_ready(conn: Any, *, install_id: str, gaps: list[str]) -> None:
    """Terminal ``not_ready``: NAME the gaps — never an error page, never a faked pct."""
    conn.execute(
        """
        UPDATE connect_readiness
           SET status = 'not_ready',
               gaps = %s,
               states = CASE
                   WHEN states->>-1 = 'not_ready' THEN states
                   ELSE states || to_jsonb('not_ready'::text)
               END,
               updated_at = now()
         WHERE install_id = %s
        """,
        (Json(list(gaps)), install_id),
    )


def read_row(conn: Any, install_id: str) -> dict[str, Any] | None:
    """Read the full durable readiness row for one install, or ``None`` if unknown.

    Returns the canonical row (status, coverage_pct, flagged, gaps, states) — the poll's
    read model. ``flagged``/``gaps``/``states`` come back as already-decoded Python lists
    (psycopg decodes jsonb). An unknown/never-started install reads as ``None`` so the poll
    can answer with the honest ``connecting`` default rather than inventing a row.
    """
    row = conn.execute(
        """
        SELECT install_id, tenant_id, repo_url, status, coverage_pct,
               flagged, gaps, states
          FROM connect_readiness
         WHERE install_id = %s
        """,
        (install_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "install_id": row[0],
        "tenant_id": row[1],
        "repo_url": row[2],
        "status": row[3],
        "coverage_pct": float(row[4]),
        "flagged": _as_list(row[5]),
        "gaps": _as_list(row[6]),
        "states": _as_list(row[7]),
    }


def _as_list(value: Any) -> list[Any]:
    """Normalise a jsonb column to a Python list (psycopg may hand back a str or list)."""
    if value is None:
        return []
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value)
