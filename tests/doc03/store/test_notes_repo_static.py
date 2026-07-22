"""AC-STORE static + pure-logic checks — the tiers that need NO live infra.

These run UNCONDITIONALLY (no skip): the ORM-free import audit (AC-STORE-04), the
boot-reaper SQL structural check (AC-STORE-07/08 static half), and the raw
order-sensitivity fold proof (AC-STORE-05 pure half). They lock the code's shape
so the divergence a divergent Postgres could hide (wrong driver, wrong heartbeat
column, non-deterministic order) is caught with no database.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap

from db.repos import notes as notes_repo

_NOTES_SRC = pathlib.Path(notes_repo.__file__)
_LIVE_PLANE_FILES = [_NOTES_SRC]


def _code_of(func: object) -> str:
    """Return a function's source with its DOCSTRING stripped.

    The substring audits below check the actual code (SQL text, column refs),
    never the prose that describes the anti-pattern. Without this, a docstring
    that names ``meetings.last_heartbeat_at`` to say "we must NOT use it" would
    make the audit match on the explanation instead of a real reference —
    a test passing/failing for the wrong reason. Stripping the docstring node
    keeps the audit honest: it sees only executable code + SQL literals.
    """
    src = textwrap.dedent(inspect.getsource(func))  # type: ignore[arg-type]
    tree = ast.parse(src)
    node = tree.body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
        body[0].value, ast.Constant
    ) and isinstance(body[0].value.value, str):
        body = body[1:]  # drop the docstring expression
    return "\n".join(ast.unparse(stmt) for stmt in body)


# -- AC-STORE-04 -- asyncpg only; no ORM import on the live plane --------------
_ORM_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(sqlalchemy|tortoise|peewee|sqlmodel|django)\b",
    re.MULTILINE,
)


def test_store_04_no_orm_import_on_live_plane() -> None:
    """T-STORE-04: zero ORM imports in the NotesRepository/TranscriptRepository seam."""
    for path in _LIVE_PLANE_FILES:
        text = path.read_text(encoding="utf-8")
        hits = _ORM_IMPORT_RE.findall(text)
        assert hits == [], "ORM import(s) on the live plane: " + repr(hits) + " in " + str(path)


def test_store_04_asyncpg_is_the_driver() -> None:
    """T-STORE-04: the store seam speaks asyncpg — parameterised, no ORM session.

    ``db.repos.notes`` carries raw SQL over a borrowed asyncpg connection (the
    facade in ``db.database`` is the single asyncpg pool site); the module itself
    imports no ORM and the whole live plane imports/uses asyncpg as the sole
    driver (verified at the pool-construction seam in ``db.database``)."""
    from db import database as db_database

    facade_src = inspect.getsource(db_database)
    assert "import asyncpg" in facade_src
    assert "asyncpg.create_pool" in facade_src
    # The store's own module carries no ORM import either.
    notes_src = _NOTES_SRC.read_text(encoding="utf-8")
    assert _ORM_IMPORT_RE.findall(notes_src) == []


# -- AC-STORE-07 / -08 (static half) -- reaper JOINs operation_runs ------------
def _reaper_sql() -> str:
    # Code-only (docstring stripped) so the audit sees the SQL, not the prose
    # that names the anti-pattern column to warn against it.
    return _code_of(notes_repo.reap_orphaned_meetings)


def test_store_07_reaper_does_not_reference_meetings_last_heartbeat() -> None:
    """T-STORE-07 static: the reaper NEVER keys off meetings.last_heartbeat_at
    (that column does not exist); it reads operation_runs.last_heartbeat_at."""
    sql = _reaper_sql()
    # No reference qualifies last_heartbeat_at against the meetings alias `m`.
    assert "m.last_heartbeat_at" not in sql
    assert "meetings.last_heartbeat_at" not in sql
    # It DOES use the operation_runs alias `r` heartbeat column.
    assert "r.last_heartbeat_at" in sql


def test_store_07_reaper_joins_operation_runs_on_scope_id_cast() -> None:
    """T-STORE-07 static: the JOIN is scope_id = meeting_id::text on the
    'meeting-harness' operation, matching the one documented section 11.2 cast."""
    sql = _reaper_sql()
    assert "operation_runs" in sql
    assert "r.scope_id" in sql and "m.id::text" in sql
    assert "'meeting-harness'" in sql


def test_store_08_reaper_covers_interrupted_and_running_but_stale() -> None:
    """T-STORE-08 static: the reaper marks a meeting 'interrupted' when its op row
    is 'interrupted' OR 'running'-but-stale (heartbeat older than the window)."""
    sql = _reaper_sql()
    assert "status = 'interrupted'" in sql   # target write
    assert "m.status = 'live'" in sql        # only live meetings are candidates
    assert "r.status = 'interrupted'" in sql
    assert "r.status = 'running'" in sql
    assert "interval '5 minutes'" in sql     # the staleness window (PART II.1)


# -- AC-STORE-05 (pure half) -- id-order is the correctness axis ---------------
def _raw_fold(deltas: list[dict[str, object]]) -> dict[str, object]:
    state: dict[str, object] = {}
    for d in deltas:
        eid = str(d["entry_id"])
        op = str(d["op"])
        payload = dict(d["payload"])  # type: ignore[arg-type]
        if op == "add":
            state[eid] = dict(payload)
        elif op == "patch":
            base = dict(state.get(eid, {}))  # patch-before-add -> empty base
            base.update(payload)
            state[eid] = base
    return state


def test_store_05_pure_fold_is_deterministic_and_order_sensitive() -> None:
    """T-STORE-05 pure: over RAW ordered rows the left-fold is deterministic, and
    reversing (patch before add) diverges — id-order is load-bearing (the DB
    integration half of this criterion is skipped; this locks the pure oracle)."""
    ordered = [
        {"id": 1, "entry_id": "E1", "op": "add", "payload": {"v": 1}},
        {"id": 2, "entry_id": "E1", "op": "patch", "payload": {"v": 2}},
    ]
    golden = _raw_fold(ordered)
    assert golden == {"E1": {"v": 2}}
    assert _raw_fold(ordered) == golden                      # deterministic re-run
    assert _raw_fold(list(reversed(ordered))) != golden      # reversed diverges


def test_store_05_load_deltas_sql_orders_by_id() -> None:
    """T-STORE-05 static: load_deltas replays in ascending id order (the write
    order the (meeting_id, id) index makes cheap)."""
    src = _code_of(notes_repo.load_deltas)
    assert "ORDER BY id" in src
    assert "FROM note_deltas" in src


# -- AC-STORE-02 (static half) -- ON CONFLICT DO NOTHING on the 4-tuple --------
def test_store_02_append_uses_on_conflict_do_nothing_on_full_key() -> None:
    """T-STORE-02 static: append_delta's ON CONFLICT names the full
    (meeting_id, window_start_s, entry_id, op) key and DO NOTHING (dup no-op)."""
    src = _code_of(notes_repo.append_delta)
    assert "ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING" in src


# -- AC-STORE-01 (static half) -- insert omits status so DEFAULT applies -------
def test_store_01_insert_segment_omits_status_when_none() -> None:
    """T-STORE-01 static: when status is None the column is OMITTED from the
    INSERT so Postgres applies its DEFAULT ('pending'), never a client literal."""
    src = _code_of(notes_repo.insert_segment)
    # The default-path INSERT lists columns WITHOUT `status`.
    assert "INSERT INTO transcript_segments (meeting_id, speaker, text, start_s, end_s)" in src
