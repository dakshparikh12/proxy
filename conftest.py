"""Repo-root pytest configuration.

Two responsibilities, both environment wiring (no product code lives here):

1. ``services`` stays a namespace package (no ``services/__init__.py``) so
   importing a service never writes ``services/__pycache__`` — which would
   otherwise appear as a sixth entry under ``services/`` and break the exact-set
   check in ``tests/doc00/test_m01_repo.py::test_repo_006``. The harness-hosted
   ``control_plane`` deployable-assembly is exposed at ``services.control_plane``
   by extending the namespace ``__path__`` to ``services/harness/src`` (where the
   assembly lives). AC-REPO-006 fixes ``services/*`` to exactly five directories,
   so ``control_plane`` is a package-config exposure only, never a sixth dir.

2. The Doc-00 durable-substrate tests (tests/doc00/test_m03_sub.py) exercise a
   real Postgres via ``_support._local_dsn()`` / ``pg_conn()``. Most bodies SKIP
   when no local database is reachable, but ``test_sub_014`` connects directly
   and REQUIRES one. This module makes the suite self-sufficient on the build
   host: if ``TEST_DATABASE_URL`` is not already set, it reuses (or, best-effort,
   provisions and starts) a throwaway local Postgres and exports the DSN so the
   substrate is verified for real. Everything here is wrapped so a missing
   Postgres toolchain never breaks collection — the DB-optional tests then skip.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time


def _wire_control_plane() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    harness_src = os.path.join(root, "services", "harness", "src")
    if not os.path.isdir(harness_src):
        return
    import services  # namespace package (no __init__.py)

    current = list(getattr(services, "__path__", []))
    if harness_src not in current:
        services.__path__ = current + [harness_src]  # type: ignore[attr-defined]


def _wire_libs_lint() -> None:
    """Expose ``libs.lint`` (the naming lint) without adding a ``libs/`` subdir.

    The lint's real code lives under ``libs/ops/src/lint`` (single-concern, inside
    an existing lib). Extending the ``libs`` namespace ``__path__`` to include
    ``libs/ops/src`` makes ``libs.lint`` importable while keeping the fixed
    six-package ``libs`` set (AC-REPO-007) and an empty ``libs/`` top level (no
    ``libs/__pycache__``). Mirrors the ``services.control_plane`` exposure above.
    """
    root = os.path.dirname(os.path.abspath(__file__))
    ops_src = os.path.join(root, "libs", "ops", "src")
    if not os.path.isdir(os.path.join(ops_src, "lint")):
        return
    import libs  # namespace package (no __init__.py)

    current = list(getattr(libs, "__path__", []))
    if ops_src not in current:
        libs.__path__ = current + [ops_src]  # type: ignore[attr-defined]


# Throwaway local test-Postgres coordinates (build host only).
_PG_DATA = "/tmp/proxy_pgtest/data"  # noqa: S108 - ephemeral test fixture dir
_PG_SOCK = "/tmp/proxy_pgtest/sock"  # noqa: S108
_PG_PORT = "55432"
_PG_USER = "proxy"
_PG_DB = "proxy_test"
_PG_DSN = f"postgresql://{_PG_USER}@localhost:{_PG_PORT}/{_PG_DB}"


def _dsn_reachable(dsn: str) -> bool:
    try:
        import psycopg
    except Exception:
        return False
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


def _find_pg_bin() -> str | None:
    for candidate in (
        "/Library/PostgreSQL/18/bin",
        "/Library/PostgreSQL/17/bin",
        "/Library/PostgreSQL/16/bin",
        "/Library/PostgreSQL/15/bin",
        "/opt/homebrew/opt/postgresql@15/bin",
        "/usr/lib/postgresql/15/bin",
    ):
        if os.path.exists(os.path.join(candidate, "pg_ctl")):
            return candidate
    pg_ctl = shutil.which("pg_ctl")
    return os.path.dirname(pg_ctl) if pg_ctl else None


def _ensure_local_postgres() -> None:
    # An explicit, already-usable DSN always wins.
    if os.environ.get("TEST_DATABASE_URL", "").strip():
        return
    if _dsn_reachable(_PG_DSN):
        os.environ["TEST_DATABASE_URL"] = _PG_DSN
        return

    pg_bin = _find_pg_bin()
    if pg_bin is None:
        return  # no toolchain — DB-optional tests will skip

    try:
        os.makedirs(_PG_SOCK, exist_ok=True)
        if not os.path.exists(os.path.join(_PG_DATA, "PG_VERSION")):
            os.makedirs(_PG_DATA, exist_ok=True)
            subprocess.run(
                [os.path.join(pg_bin, "initdb"), "-D", _PG_DATA, "-U", _PG_USER,
                 "--auth=trust"],
                check=True, capture_output=True, text=True, timeout=120,
            )
        subprocess.run(
            [os.path.join(pg_bin, "pg_ctl"), "-D", _PG_DATA, "-o",
             f"-k {_PG_SOCK} -p {_PG_PORT} -c listen_addresses=localhost",
             "-w", "-l", "/tmp/proxy_pgtest/run.log", "start"],  # noqa: S108
            check=False, capture_output=True, text=True, timeout=60,
        )
        for _ in range(20):
            if _dsn_reachable(f"postgresql://{_PG_USER}@localhost:{_PG_PORT}/postgres"):
                break
            time.sleep(0.5)
        subprocess.run(
            [os.path.join(pg_bin, "createdb"), "-h", _PG_SOCK, "-p", _PG_PORT,
             "-U", _PG_USER, _PG_DB],
            check=False, capture_output=True, text=True, timeout=30,
        )
        if _dsn_reachable(_PG_DSN):
            os.environ["TEST_DATABASE_URL"] = _PG_DSN
    except Exception:
        return  # best-effort; DB-optional tests skip if this failed


_wire_control_plane()
_wire_libs_lint()
try:
    _ensure_local_postgres()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Test isolation for the import-time contracts registry (environment wiring).
#
# ``libs.contracts.CHANNEL_REGISTRY`` is a MODULE-GLOBAL populated at import
# time by ``ProxyMessage.__init_subclass__`` (auto-registration). A test that
# DEFINES a throwaway ``ProxyMessage`` subclass (e.g. the AC-REG-001 probe)
# permanently mutates that global for the rest of the session, which would leak
# a registry-only orphan into every subsequent closed-graph assertion. This is
# a shared-mutable-global hygiene concern, not product behaviour, so the reset
# lives here (never in a product module and never in a sealed test): snapshot
# the registry before each test and restore it after, so every test observes
# the canonical (import-time) registry regardless of what a prior test defined.
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_contracts_registry():
    """Snapshot/restore CHANNEL_REGISTRY around each test (global hygiene)."""
    try:
        from libs.contracts import CHANNEL_REGISTRY
    except Exception:
        # Product not built yet / not importable in this run — nothing to isolate.
        yield
        return
    snapshot = dict(CHANNEL_REGISTRY)
    try:
        yield
    finally:
        CHANNEL_REGISTRY.clear()
        CHANNEL_REGISTRY.update(snapshot)
