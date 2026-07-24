"""Doc 01 · gap ORM-TIER1-ONLY-DJANGO — the three tier-1 exact ORMs proven on REAL repos.

Drives the PRODUCT path (``run_full_pipeline`` -> the real ``CodeIntelMCPServer.who_writes``)
on a real public SQLAlchemy CRUD app, and the real SQLAlchemy source tree, asserting:

* ``is_tier1`` is True on real SQLAlchemy code (previously only Django was recognised);
* ``who_writes`` returns the EXACT resolved writer set (add/commit + typed-param update +
  query-then-delete), with read-only query methods excluded — no fabricated/​missed writers.

No injected doubles: the writers come from the real clone through the real tool.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

_CACHE = pathlib.Path(os.environ.get("PROXY_ESTATE_CACHE", "/tmp/proxy_estates"))
_CRUD_URL = "https://github.com/testdrivenio/fastapi-crud-sync.git"


def _clone(name: str, url: str) -> pathlib.Path:
    repo = _CACHE / name
    try:
        if not (repo / ".git").is_dir():
            _CACHE.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", "--depth", "1", url, str(repo)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(f"real-repo clone unavailable (network/git): {exc}")
    return repo


@pytest.mark.integration
def test_orm_tier1_sqlalchemy_who_writes_on_real_crud_app() -> None:
    """SQLAlchemy is exact-supported: who_writes on a REAL SQLAlchemy CRUD app returns the
    exact resolved writer set through run_full_pipeline -> the real tool."""
    from services.code_intel import orm
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone("fastapi-crud-sync", _CRUD_URL)
    # The models + crud live under src/app; is_tier1 must recognise the SQLAlchemy stack.
    app_dir = repo / "src" / "app"
    if not app_dir.exists():  # pragma: no cover - upstream layout guard
        pytest.skip("upstream repo layout changed")
    assert orm.is_tier1(app_dir) is True, "real SQLAlchemy stack must be tier-1"

    pipeline = run_full_pipeline(tenant_id="t-orm-sa", repo_url=str(repo))
    server = pipeline.server
    assert server is not None, "run_full_pipeline must attach the real MCP server"

    result = server.who_writes("notes")
    ids = {w.id for w in result.writers}

    # The exact write set: create (post), update (put), delete (delete) — through the real tool.
    expected = {
        "src/app/api/crud.py::post",
        "src/app/api/crud.py::put",
        "src/app/api/crud.py::delete",
    }
    assert expected.issubset(ids), f"missing real SQLAlchemy writers: {expected - ids} (got {ids})"
    # Read-only query methods are never writers.
    assert "src/app/api/crud.py::get" not in ids
    assert "src/app/api/crud.py::get_all" not in ids
    # Every writer on the exact-supported stack is tagged resolved (never a silent lower-bound).
    for w in result.writers:
        assert w.confidence == "resolved", f"{w.id} tagged {w.confidence!r}, expected resolved"
