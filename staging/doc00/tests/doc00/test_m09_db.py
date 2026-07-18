"""Doc 00 · §11 The database layer (AC-DB-001..004).

Milestone m09. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/db`` exists.

Oracle sources per PROTO-DETERMINISTIC-01:

  * AC-DB-001 [integration]  -- static scan of the asyncpg pool-construction
    site: canonical kwargs (min_size=2, max_size=20,
    max_inactive_connection_lifetime=30, command_timeout=10) + a Cloud SQL Auth
    Proxy Unix-socket DSN with no SSL params.
  * AC-DB-002 [static]  -- import scan proving a single ``Database`` facade owns
    the pool + a ``repos`` namespace of thin per-domain repositories, all SQL is
    parameterized, and NO ORM (SQLAlchemy models / Django ORM) is imported.
  * AC-DB-003 [contract]  -- schema check over ``information_schema.columns`` of
    the migrated DB: every app-table ``meeting_id`` is ``uuid``; the sole ``text``
    exception is ``operation_runs.scope_id``; the atomic-claim site casts
    ``meeting_id::text`` exactly once.
  * AC-DB-004 [integration]  -- Alembic config check: revision-DAG migrations
    (not epoch-filename), an ``env.py`` advisory lock wrapping ``run_migrations``,
    and the parallel-boot retry-loop CMD (§9).
"""

import re

import pytest

import _support as S


# The five app tables whose meeting handle MUST be typed uuid (spec §11 / §11.2).
# meetings.id is the primary key; the rest carry a meeting_id FK column.
_MEETING_ID_TABLES = {
    "meetings": "id",
    "meeting_cost": "meeting_id",
    "staged_drafts": "meeting_id",
    "transcript_segments": "meeting_id",
    "note_deltas": "meeting_id",
}


def _pool_construction_source() -> tuple[str, str]:
    """Return (relpath, source-window) around the asyncpg.create_pool() site.

    Fails red (empty) before libs/db exists. The window is the source file that
    contains the single create_pool() call, so kwarg/DSN assertions read the
    real construction site rather than the whole tree.
    """
    hits = S.grep_python(r"create_pool\s*\(")
    if not hits:
        return "", ""
    relpath, _lineno, _line = hits[0]
    src = S.read_text(*relpath.split("/")) or ""
    return relpath, src


# ── AC-DB-001 ─────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_db_001_asyncpg_pool_canonical_args():
    """AC-DB-001: asyncpg pool created with min_size=2, max_size=20, lifetime=30, command_timeout=10; unix-socket DSN, no SSL."""
    from libs import db  # noqa: F401  -- red before the db package exists

    calls = S.grep_python(r"create_pool\s*\(")
    assert calls, "no asyncpg.create_pool() call site found (product not built)"
    assert len(calls) == 1, f"the pool must be constructed exactly once; found {len(calls)}: {calls}"

    relpath, src = _pool_construction_source()
    assert "asyncpg" in src, f"the pool must be an asyncpg pool (create_pool in {relpath}): {relpath}"

    # Canonical kwargs -- pool_arg_mismatch threshold is 0, so every value is exact.
    for kwarg, value in (
        ("min_size", "2"),
        ("max_size", "20"),
        ("max_inactive_connection_lifetime", "30"),
        ("command_timeout", "10"),
    ):
        assert re.search(rf"\b{kwarg}\s*=\s*{value}\b", src), (
            f"create_pool() must pass {kwarg}={value} (canonical §11 pool config); not found in {relpath}"
        )

    # DSN is a Cloud SQL Auth Proxy Unix socket (host=/cloudsql/...) with NO SSL.
    assert re.search(r"/cloudsql/", src) or re.search(r"host\s*=\s*/", src), (
        f"DSN must be a Cloud SQL Auth Proxy Unix socket (host=/cloudsql/...); not evidenced in {relpath}"
    )
    assert not re.search(r"\bssl\s*=", src) and "sslmode" not in src, (
        f"the unix-socket DSN must carry no SSL params (proxy terminates TLS); SSL param present in {relpath}"
    )


# ── AC-DB-002 ─────────────────────────────────────────────────────────────
@pytest.mark.static
def test_db_002_single_facade_repos_parameterized_no_orm():
    """AC-DB-002: one Database facade owns the pool + repos namespace; SQL parameterized; NO ORM."""
    from libs import db  # noqa: F401  -- red before the db package exists

    # Exactly one Database facade class owns the pool.
    facade_defs = S.count_def_sites("Database")
    assert facade_defs, "no Database facade class found (product not built)"
    assert len(facade_defs) == 1, f"there must be exactly one Database facade; found {facade_defs}"
    assert facade_defs[0].startswith("libs/db/"), f"the Database facade must live in libs/db; found {facade_defs[0]}"

    # It exposes a repos namespace of thin per-domain repositories.
    db_src = S.read_all_text("*.py", root_parts=("libs", "db"))
    assert re.search(r"\brepos\b", db_src), "the Database facade must expose a `repos` namespace"
    for repo in (
        "MeetingRepository",
        "TranscriptRepository",
        "NotesRepository",
        "SandboxRepository",
        "OperationRepository",
    ):
        assert S.count_def_sites(repo), f"missing per-domain repository: {repo}"

    # NO ORM: no SQLAlchemy / Django-ORM imports anywhere in the product tree
    # (orm_imports threshold = 0).
    orm_imports = S.grep_python(
        r"^\s*(?:from|import)\s+(?:sqlalchemy|sqlmodel|tortoise|peewee|django\.db|pony)\b"
    )
    assert not orm_imports, f"no ORM may be imported (orm_imports must be 0): {orm_imports}"

    # All SQL is parameterized -- no value interpolation into SQL text
    # (interpolated_sql_sites threshold = 0). Flag f-string/%-concat SQL.
    fstring_sql = S.grep_python(r"""(?i)f["'].*\b(?:select|insert|update|delete)\b.*\{""")
    percent_sql = S.grep_python(r"""(?i)["'].*\b(?:select|insert|update|delete)\b.*["']\s*%\s*[\w(]""")
    interpolated = fstring_sql + percent_sql
    assert not interpolated, f"all SQL must be parameterized (no interpolation): {interpolated}"


# ── AC-DB-003 ─────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_db_003_meeting_id_uuid_everywhere_scope_id_text():
    """AC-DB-003: meeting_id is uuid across every app table; only operation_runs.scope_id is text; one ::text cast."""
    from libs import db  # noqa: F401  -- red (product), skip (no DB), imported FIRST

    # The atomic-claim call site casts meeting_id::text exactly once (§5.2).
    casts = S.grep_python(r"meeting_id\s*::\s*text")
    assert casts, "no meeting_id::text cast found at the atomic-claim call site (product not built)"
    assert len(casts) == 1, f"meeting_id::text must be cast exactly once (one call site); found {len(casts)}: {casts}"

    with S.pg_conn() as conn:
        # DB reachable only after the product exists -> migrate, then inspect types.
        info = conn.info
        parts = [f"dbname={info.dbname}", f"user={info.user}"]
        if info.host:
            parts.append(f"host={info.host}")
        if info.port:
            parts.append(f"port={info.port}")
        dsn = " ".join(parts)

        result = S.apply_migrations(dsn)
        assert result.returncode == 0, f"alembic upgrade head failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"

        # Every app-table meeting handle is uuid (meeting_id_type_mismatch = 0).
        for table, column in _MEETING_ID_TABLES.items():
            assert S.table_exists(conn, table), f"migrated schema missing table {table!r}"
            cols = S.table_columns(conn, table)
            assert column in cols, f"{table} missing meeting handle column {column!r}: {sorted(cols)}"
            assert cols[column] == "uuid", (
                f"{table}.{column} must be uuid across every app table (§11.2); got {cols[column]!r}"
            )

        # operation_runs.scope_id is the SOLE text exception (scope_id_not_text = 0).
        assert S.table_exists(conn, "operation_runs"), "migrated schema missing operation_runs"
        op_cols = S.table_columns(conn, "operation_runs")
        assert "scope_id" in op_cols, f"operation_runs missing scope_id: {sorted(op_cols)}"
        assert op_cols["scope_id"] == "text", (
            f"operation_runs.scope_id must stay text (holds meeting_id or task_id); got {op_cols['scope_id']!r}"
        )


# ── AC-DB-004 ─────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_db_004_alembic_revision_dag_advisory_lock_retry_cmd():
    """AC-DB-004: Alembic migrations (revision DAG) with env.py advisory lock and parallel-boot retry-loop CMD."""
    from libs import db  # noqa: F401  -- red before the db package exists

    # Alembic config present (revision DAG, not an epoch-filename scheme).
    alembic_ini = S.read_text("alembic.ini")
    assert alembic_ini is not None, "alembic.ini not found (Alembic migrations not set up)"

    env_py = S.read_all_text("env.py", root_parts=("migrations",)) or S.read_all_text("env.py", root_parts=("alembic",))
    assert env_py.strip(), "Alembic env.py not found (migrations/ or alembic/)"

    # Revision DAG: migration scripts carry revision / down_revision identifiers,
    # NOT epoch-timestamp filenames.
    versions_src = (
        S.read_all_text("*.py", root_parts=("migrations", "versions"))
        or S.read_all_text("*.py", root_parts=("alembic", "versions"))
    )
    assert versions_src.strip(), "no Alembic version scripts found (migrations/versions/)"
    assert re.search(r"^\s*revision\s*[:=]", versions_src, re.M), "version scripts must declare a `revision` (DAG node)"
    assert re.search(r"^\s*down_revision\s*[:=]", versions_src, re.M), (
        "version scripts must declare `down_revision` (DAG edge) -- revision DAG, not epoch-filename"
    )

    # env.py wraps run_migrations in a pg_advisory_lock (missing_advisory_lock_in_env = 0).
    assert "pg_advisory_lock" in env_py, (
        "env.py must wrap the upgrade in SELECT pg_advisory_lock(...) (Alembic doesn't lock by default)"
    )
    assert "run_migrations" in env_py, "env.py must call run_migrations (guarded by the advisory lock)"

    # The parallel-boot retry-loop CMD (§9): `until alembic upgrade head; do ... sleep 5; done`.
    dockerfiles = S.read_all_text("Dockerfile*", root_parts=()) + S.read_all_text("*.dockerfile", root_parts=())
    assert re.search(r"until\s+alembic\s+upgrade\s+head", dockerfiles), (
        "the retry-loop CMD (`until alembic upgrade head; do ...`) must be present for parallel-boot safety (§9)"
    )
