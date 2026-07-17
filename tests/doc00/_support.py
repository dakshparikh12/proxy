"""Shared, stdlib-only support for the Doc 00 evidence layer.

Design rules (enforced across every test_m*.py in this package):

  * This module imports ONLY the Python standard library. It MUST be importable
    at pytest collection time even though NO product code exists yet. Every
    product import (``libs.*`` / ``services.*``) happens INSIDE a test body, so
    the suite COLLECTS clean and FAILS red before the product is built.
  * All path resolution is anchored at the git repository root (``repo_root()``)
    so the tests work identically whether run from ``staging/doc00/tests`` or
    after the conductor promotes them to ``tests/doc00``.
  * Deployment / IaC / Docker / CI criteria are settled by static text oracles
    over the product's committed source (``infra/``, ``deploy/``,
    ``.github/workflows/``). These are deterministic and hermetic -- no
    terraform/docker binary required -- and go red before the files exist.
  * Postgres-backed (schema / model-stateful) criteria use ``pg_conn()`` which
    resolves a *local* test database. Product code is always imported FIRST in
    those test bodies, so absence-of-product is reported as a red failure and
    absence-of-database (only reachable once the product exists) as a skip.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Repository-root anchored filesystem helpers
# ---------------------------------------------------------------------------

_THIS = pathlib.Path(__file__).resolve()


def repo_root() -> pathlib.Path:
    """Walk up from this file until a ``.git`` marker is found."""
    for parent in (_THIS, *_THIS.parents):
        if (parent / ".git").exists():
            return parent
    # Fallback for the historical layout .../tests/doc00/_support.py
    return _THIS.parents[3]


ROOT = repo_root()


def rel(*parts: str) -> pathlib.Path:
    return ROOT.joinpath(*parts)


def exists(*parts: str) -> bool:
    return rel(*parts).exists()


def read_text(*parts: str) -> str | None:
    """Return file contents relative to repo root, or ``None`` if absent."""
    p = rel(*parts)
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def list_dir(*parts: str) -> set[str]:
    """Names of entries directly under a repo-relative dir (empty set if absent)."""
    p = rel(*parts)
    if not p.is_dir():
        return set()
    return {c.name for c in p.iterdir()}


def list_subdirs(*parts: str) -> set[str]:
    p = rel(*parts)
    if not p.is_dir():
        return set()
    return {c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")}


def glob(pattern: str, *, root_parts: tuple[str, ...] = ()) -> list[pathlib.Path]:
    base = rel(*root_parts)
    if not base.exists():
        return []
    return sorted(base.rglob(pattern))


def read_all_text(pattern: str, *, root_parts: tuple[str, ...] = ()) -> str:
    """Concatenate the text of every file matching ``pattern`` under a subtree."""
    chunks = []
    for f in glob(pattern, root_parts=root_parts):
        try:
            chunks.append(
                f"# === {f.relative_to(ROOT)} ===\n"
                + f.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, UnicodeError):
            continue
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Python source scanning (stdlib only -- independent of the product toolchain)
# ---------------------------------------------------------------------------

# The product's own importable source lives under these top-level trees.
PRODUCT_TREES = ("services", "libs")


def python_files(*, trees: tuple[str, ...] = PRODUCT_TREES) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for t in trees:
        out.extend(glob("*.py", root_parts=(t,)))
    return out


def grep_python(
    pattern: str, *, trees: tuple[str, ...] = PRODUCT_TREES, flags: int = 0
) -> list[tuple[str, int, str]]:
    """Return ``(relpath, lineno, line)`` for every product-source line matching."""
    rx = re.compile(pattern, flags)
    hits: list[tuple[str, int, str]] = []
    for f in python_files(trees=trees):
        try:
            for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if rx.search(line):
                    hits.append((str(f.relative_to(ROOT)), i, line.rstrip()))
        except OSError:
            continue
    return hits


def count_def_sites(func_name: str, *, trees: tuple[str, ...] = PRODUCT_TREES) -> list[str]:
    """Files that define ``def``/``async def``/``class`` ``func_name``."""
    rx = re.compile(rf"^\s*(?:async\s+def|def|class)\s+{re.escape(func_name)}\b")
    files: list[str] = []
    for f in python_files(trees=trees):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(rx.match(ln) for ln in text.splitlines()):
            files.append(str(f.relative_to(ROOT)))
    return files


# ---------------------------------------------------------------------------
# Goldens (mechanically derived answer keys)
# ---------------------------------------------------------------------------

_GOLDEN_DIRS = (
    _THIS.parent.parent / "goldens",       # .../doc00/goldens (pre-promotion, tests one level down)
    _THIS.parent.parent.parent / "goldens",
    ROOT / "goldens",                       # <root>/goldens (post-promotion)
    ROOT / "acceptance" / "doc00" / "goldens",
    ROOT / "staging" / "doc00" / "goldens",
)


def load_golden(name: str) -> dict:
    """Load a derived golden JSON by filename (searches known golden dirs)."""
    for d in _GOLDEN_DIRS:
        p = d / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"golden {name!r} not found in {[str(d) for d in _GOLDEN_DIRS]}"
    )


# ---------------------------------------------------------------------------
# Postgres (schema / model-stateful oracles)
# ---------------------------------------------------------------------------


def _local_dsn() -> str | None:
    """A DSN pointing at a reachable *local* test Postgres, or None."""
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        v = os.environ.get(var, "").strip()
        # Only honour local/TCP DSNs for tests; the prod unix-socket DSN
        # (host=/cloudsql/...) is not a test target.
        if v.startswith(("postgresql://", "postgres://")) and "@/" not in v and "cloudsql" not in v:
            return v
    return None


@contextlib.contextmanager
def pg_conn():
    """Yield a psycopg3 autocommit connection to a local test Postgres.

    Skips (not fails) when no local Postgres is reachable. Callers MUST import
    the product under test *before* entering this context, so that missing
    product is a red failure and missing database (only reachable post-build)
    is an explicit skip.
    """
    import pytest  # available at test time

    dsn = _local_dsn()
    server = None
    if dsn is None:
        try:
            import testing.postgresql  # type: ignore
        except Exception:
            raise pytest.skip.Exception(
                "no local Postgres (set TEST_DATABASE_URL or install testing.postgresql)"
            )
        server = testing.postgresql.Postgresql()
        dsn = server.url()
    try:
        import psycopg  # lazy; product installs it
    except Exception:
        if server is not None:
            server.stop()
        raise pytest.skip.Exception("psycopg not installed in this environment")
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()
        if server is not None:
            server.stop()


def apply_migrations(dsn: str) -> subprocess.CompletedProcess:
    """Run the product's Alembic migrations to head against ``dsn``.

    Fails red before the product exists (no ``alembic.ini`` / migrations).
    """
    env = dict(os.environ, DATABASE_URL=dsn)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )


def table_columns(conn, table: str) -> dict[str, str]:
    """{column_name: data_type} for a table in the connected database."""
    cur = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def table_exists(conn, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
    )
    return cur.fetchone() is not None


def index_defs(conn, table: str) -> dict[str, str]:
    """{indexname: indexdef} for a table (Postgres pg_indexes)."""
    cur = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", (table,)
    )
    return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# TOML tunables (config/defaults.toml -- read via stdlib tomllib)
# ---------------------------------------------------------------------------


def load_defaults_toml() -> dict:
    text = read_text("config", "defaults.toml")
    if text is None:
        raise FileNotFoundError("config/defaults.toml not found (product not built)")
    import tomllib
    return tomllib.loads(text)
