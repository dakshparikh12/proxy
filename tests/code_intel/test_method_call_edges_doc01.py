"""Doc 01 · G7 — method / attribute call edges are extracted, tagged lower-bound.

The GAP (G7-CALL-EDGES-DROP-METHOD-CALLS): ``_DeclVisitor.visit_Call`` only
recorded a ``calls`` edge when the callee was an ``ast.Name``. Attribute / method
calls — ``self.foo()``, ``obj.method()``, ``pkg.func()`` (``ast.Attribute``) —
were silently dropped, so the call graph systematically omitted every qualified
call and ``get_dependents`` under-reported blast radius while still tagging its
results ``resolved`` (a Law-2 violation: a search-derived lower-bound presented
as exhaustive).

This is the FIRST-written failing acceptance test. It drives the PRODUCT path —
``run_full_pipeline`` on the pinned real flask clone — and the REAL tool
(``pipeline.server.get_dependents``). No injected doubles: the edge must be
extracted by the real builder, persisted through the real store, and surfaced by
the real tool with the correct honesty tag.

Concrete real-repo fact (flask @ 36e4a824):
  * ``full_dispatch_request`` is defined in ``src/flask/app.py`` (a method).
  * ``wsgi_app`` (same file) calls ``self.full_dispatch_request(ctx)`` — an
    ``ast.Attribute`` (method) call.
Before the fix that call edge did not exist, so ``wsgi_app`` was NOT a dependent
of ``full_dispatch_request``. After the fix it IS — and because the edge was
recovered by trailing-attr-name heuristic (not a resolved referent binding), the
dependent is tagged ``lower-bound``, never ``resolved``.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess

import pytest

_CACHE = pathlib.Path(os.environ.get("PROXY_ESTATE_CACHE", "/tmp/proxy_estates"))
_FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"


def _clone_estate(name: str, url: str, sha: str) -> pathlib.Path:
    repo = _CACHE / name
    try:
        if not (repo / ".git").is_dir():
            _CACHE.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", url, str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", sha], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(f"estate clone unavailable (network/git): {exc}")
    return repo


@pytest.mark.integration
def test_g7_method_call_edges_extracted_and_lower_bound_on_real_flask() -> None:
    """Method-call edges are extracted THROUGH run_full_pipeline and honestly tagged."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    pipeline = run_full_pipeline(tenant_id="t-g7", repo_url=str(repo))
    graph = pipeline.graph

    calls = [e for e in graph.edges if e.kind == "calls"]
    assert calls, "no calls edges at all"

    def _leaf(node_id: str) -> str:
        return node_id.rsplit("::", 1)[-1]

    # (1) The method-call edge wsgi_app -> full_dispatch_request now EXISTS.
    method_edges = [
        e for e in calls
        if _leaf(e.source) == "wsgi_app" and _leaf(e.target) == "full_dispatch_request"
    ]
    assert method_edges, (
        "method call edge wsgi_app -> full_dispatch_request missing — "
        "attribute/method calls are still being dropped from the graph"
    )
    for e in method_edges:
        assert e.file_path and e.line > 0, f"method-call edge lacks a site: {e}"

    # (2) That edge is PERSISTED through the real store into the canonical schema.
    conn = sqlite3.connect(str(pipeline.graph_db_path))
    try:
        persisted = conn.execute(
            "SELECT COUNT(*) FROM graph_edges "
            "WHERE kind='calls' AND source LIKE '%::wsgi_app' "
            "AND target LIKE '%::full_dispatch_request'"
        ).fetchone()[0]
        assert persisted >= 1, "method-call edge not persisted to graph_edges"
    finally:
        conn.close()

    # (3) The REAL tool surfaces the recovered dependent — AND tags it honestly.
    result = pipeline.server.get_dependents("full_dispatch_request", limit=200)
    wsgi = [r for r in result.results if _leaf(r.id) == "wsgi_app"]
    assert wsgi, (
        "wsgi_app not returned as a dependent of full_dispatch_request — "
        "the recovered method-call edge did not reach get_dependents"
    )
    for r in wsgi:
        assert r.confidence == "lower-bound", (
            f"dependent {r.id} reached via a method-call edge is tagged "
            f"{r.confidence!r}; a search/heuristic-derived edge must never be "
            f"presented as 'resolved' (Law 2 — never overstate)"
        )

    # (4) The honesty tag is DISCRIMINATING: a dependent reachable purely through
    #     name-resolved (ast.Name) call edges is still 'resolved'.
    assert any(r.confidence == "resolved" for r in _resolved_probe(pipeline)), (
        "no dependent anywhere is tagged 'resolved' — the lower-bound tag must "
        "remain discriminating (name-resolved call chains stay resolved)"
    )


def _resolved_probe(pipeline: object) -> list[object]:
    server = pipeline.server  # type: ignore[attr-defined]
    out: list[object] = []
    for sym in ("Flask", "Response", "Request", "Blueprint", "Config"):
        out.extend(server.get_dependents(sym, limit=200).results)
    return out
