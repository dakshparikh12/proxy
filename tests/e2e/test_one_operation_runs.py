"""J-09-one-operation-runs — the substrate has ONE liveness authority (Doc 09 §2, CANONICAL §2).

Two invariants, asserted end-to-end against the real artefacts (not spec prose):

  1. **One table, no shadow.** A schema/migration sweep across the WHOLE repo product
     surface (every Alembic migration + every ``libs/`` / ``services/`` source file,
     both raw-SQL ``op.execute`` DDL and the ``op.create_table(...)`` builder form)
     finds EXACTLY one ``operation_runs`` table definition and ZERO ``meeting_harness``
     table. A second liveness table is a split-brain: the reconcile sweep would reap
     one and never the other. The scan walks every candidate path on purpose — the
     node's named risk is "a shadow liveness table slips through if the scan only greps
     one path", so this test refuses to grep one path.

  2. **Reconcile reaps the orphan.** On the real migrated Postgres we seed a stale,
     orphaned ``running`` operation_runs row (last heartbeat well past STALE_AFTER_S)
     alongside a fresh one, run the product's ``run_reconcile_sweep`` (the sole
     idempotent reconcile seam, libs/ops), and assert the orphan is flipped to
     ``interrupted`` while the fresh owner is untouched — and that a second sweep over
     the reaped state is a no-op (idempotent). Not done if reconcile leaves an orphan
     claim un-reaped.

DB-backed body SKIPS (never fails) when no local Postgres is reachable — the product
is imported FIRST so absence-of-product is a red failure and absence-of-database is an
explicit skip. Run via ``build/setup-test-env.sh`` so the real DB is present.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

_THIS = pathlib.Path(__file__).resolve()


def _repo_root() -> pathlib.Path:
    for parent in (_THIS, *_THIS.parents):
        if (parent / ".git").exists():
            return parent
    return _THIS.parents[2]


ROOT = _repo_root()

# The product surface the closure scan walks: every Alembic migration + every
# workspace-member source tree. Tests/, scripts/, staging/, and the venv are NOT
# product DDL and are excluded on purpose (the DDL lives here or nowhere).
_PRODUCT_DIRS = (
    ROOT / "migrations",
    ROOT / "libs",
    ROOT / "services",
)

# Every syntactic form a table definition can take across the product surface:
#   * raw SQL DDL executed via op.execute("CREATE TABLE <name> ...")  (0001_substrate)
#   * the Alembic builder form op.create_table("<name>", ...)
#   * a bare SQL file's CREATE TABLE (incl. IF NOT EXISTS)
# The scan is case-insensitive and tolerant of intervening whitespace so a table
# defined via any of these forms is still counted (no path/form can hide a shadow).
_CREATE_TABLE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)", re.IGNORECASE
)
_CREATE_TABLE_OP_RE = re.compile(
    r"""op\.create_table\(\s*['"](\w+)['"]""", re.IGNORECASE
)


def _iter_source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for base in _PRODUCT_DIRS:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in (".py", ".sql") and "__pycache__" not in path.parts:
                files.append(path)
    return files


def _table_definition_sites(table: str) -> list[str]:
    """Every file:line in the product surface that DEFINES a table named ``table``.

    Matches both the raw-SQL ``CREATE TABLE <table>`` form and the Alembic
    ``op.create_table("<table>")`` builder form, across .py and .sql files.
    """
    sites: list[str] = []
    for path in _iter_source_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            for regex in (_CREATE_TABLE_RE, _CREATE_TABLE_OP_RE):
                m = regex.search(line)
                if m and m.group(1) == table:
                    rel = path.relative_to(ROOT)
                    sites.append(f"{rel}:{lineno}")
                    break
    return sites


# ── Invariant 1: exactly one operation_runs table; zero meeting_harness ───────


def test_one_operation_runs_table_definition_across_the_whole_repo() -> None:
    """Schema/migration sweep: EXACTLY one operation_runs DDL, whole product surface."""
    op_runs = _table_definition_sites("operation_runs")
    assert len(op_runs) == 1, (
        "there must be EXACTLY one operation_runs table definition across the repo "
        f"(a second is a split-brain liveness authority); found {len(op_runs)}: {op_runs}"
    )
    # Anchored where CANONICAL §2 locks it — the one durable-ops migration.
    assert op_runs[0].startswith("migrations/"), (
        f"the one operation_runs DDL must live in an Alembic migration; found at {op_runs[0]}"
    )


def test_zero_meeting_harness_table_anywhere() -> None:
    """No resurrected meeting_harness table (Doc 04's table is deleted — CANONICAL §2)."""
    harness = _table_definition_sites("meeting_harness")
    assert harness == [], (
        "Doc 04's separate meeting_harness table is DELETED (CANONICAL §2); a "
        f"resurrected table def is a split-brain; found: {harness}"
    )


# ── Invariant 2: reconcile reaps a seeded duplicate/orphan operation_runs row ──


def _local_dsn() -> str | None:
    """A reachable local/TCP test-Postgres DSN, or None (the prod socket DSN is not a target)."""
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        v = os.environ.get(var, "").strip()
        if v.startswith(("postgresql://", "postgres://")) and "@/" not in v and "cloudsql" not in v:
            return v
    return None


@contextlib.contextmanager
def _pg_conn(dsn: str) -> Iterator[Any]:
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _apply_migrations(dsn: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, DATABASE_URL=dsn)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )


def _stale_after_s() -> int:
    from libs.db import stale_after_s

    return int(stale_after_s())


@pytest.mark.integration
def test_reconcile_sweep_reaps_seeded_orphan_operation_run() -> None:
    """Seed a stale orphan running row on the real DB; run_reconcile_sweep reaps it.

    A fresh owner row (heartbeat now) stays 'running'; the orphaned row (heartbeat
    well past STALE_AFTER_S — the crashed-harness case CANONICAL §2 calls out) is
    flipped to 'interrupted', freeing the partial unique index for a re-claim. The
    sweep is then run a SECOND time and must be a no-op (idempotent end state).
    """
    # Product FIRST: absence-of-product is a red failure, not a skip.
    from libs.ops import run_reconcile_sweep

    dsn = _local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres reachable (set TEST_DATABASE_URL / run via build/setup-test-env.sh)")

    r = _apply_migrations(dsn)
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr or r.stdout}"

    stale_after = _stale_after_s()
    # The sync token-gated sweep is refused without a valid internal-reconcile token;
    # bind the dev default (prod binds the real secret via Secret Manager).
    token = os.environ.get("INTERNAL_RECONCILE_TOKEN") or "internal-secret"

    with _pg_conn(dsn) as conn:
        conn.execute("DELETE FROM operation_runs")
        # Fresh owner: heartbeat now — must survive the sweep.
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('m-fresh', 'meeting-harness', 'running', now())"
        )
        # Orphan: a crashed harness's row, heartbeat well past STALE_AFTER_S — must be reaped.
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('m-orphan', 'meeting-harness', 'running', now() - (%s || ' seconds')::interval)",
            (stale_after + 60,),
        )

        # Run the product reconcile seam (sync token-gated path). Returns the
        # idempotent end state: the count of rows still 'running'.
        still_running_1 = run_reconcile_sweep(conn, token=token)

        orphan = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='m-orphan'"
        ).fetchone()[0]
        fresh = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='m-fresh'"
        ).fetchone()[0]

        assert orphan == "interrupted", (
            f"the reconcile sweep MUST reap the orphaned running row (got {orphan!r}); "
            "an un-reaped orphan claim keeps the partial index locked forever"
        )
        assert fresh == "running", (
            f"a fresh, heartbeating owner must NOT be reaped (got {fresh!r})"
        )

        # No orphaned 'running' claim survives past its scope: exactly the one fresh owner remains.
        orphan_running = conn.execute(
            "SELECT count(*) FROM operation_runs "
            "WHERE status='running' AND last_heartbeat_at < now() - (%s || ' seconds')::interval",
            (stale_after,),
        ).fetchone()[0]
        assert orphan_running == 0, (
            f"reconcile left {orphan_running} orphaned running claim(s) un-reaped"
        )
        assert still_running_1 == 1, (
            f"exactly one (fresh) running row must remain after the sweep; got {still_running_1}"
        )

        # Idempotent: a second sweep over the reaped state yields the SAME end state.
        still_running_2 = run_reconcile_sweep(conn, token=token)
        assert still_running_2 == still_running_1, (
            "reconcile must be idempotent: a second sweep over the same state must "
            f"yield the same end state ({still_running_1} → {still_running_2})"
        )
        reaped_still_interrupted = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='m-orphan'"
        ).fetchone()[0]
        assert reaped_still_interrupted == "interrupted", (
            "the reaped orphan must stay interrupted across a repeat sweep; "
            f"got {reaped_still_interrupted!r}"
        )

        # Cleanup so a persistent throwaway DB does not leak fixed-id rows across runs.
        conn.execute("DELETE FROM operation_runs WHERE scope_id IN ('m-fresh','m-orphan')")
