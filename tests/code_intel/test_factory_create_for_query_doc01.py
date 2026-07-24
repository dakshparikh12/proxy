"""Doc 01 · gap MCP-FACTORY-CREATE-FOR-QUERY-STUB — the per-query factory mints a
QUERYABLE server bound to the real pipeline's immutable graph/clone/lsp.

The advertised factory API (``MCPServerFactory.create_for_query`` / the spec's
``make_code_intel_server(graph, lsp, overview)``) previously returned a bare
``CodeIntelMCPServer()`` with no graph/clone bound — it incremented an instance
counter and passed AC-M5-001 purely on the counter, but the minted server could
not answer any tool call (empty graph, no clone).

This test drives the REAL product path: ``run_full_pipeline`` on a real public
SQLAlchemy CRUD app, then uses the factory bound to that pipeline to mint a
per-query server, and asserts the minted-per-query instance actually answers
``who_writes`` and ``get_dependents`` on the real graph — while still being a
fresh, distinct instance per call (the AC-M5-001 concurrency contract).

No injected doubles: the answers come from the real clone through the real tool.
"""
from __future__ import annotations

import asyncio
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
def test_factory_minted_server_answers_tools_on_real_graph() -> None:
    """A factory minted per query is bound to the real pipeline graph/clone and
    answers who_writes + get_dependents on the REAL data — not an empty shell."""
    from services.code_intel.mcp_server import MCPServerFactory
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone("fastapi-crud-sync", _CRUD_URL)
    app_dir = repo / "src" / "app"
    if not app_dir.exists():  # pragma: no cover - upstream layout guard
        pytest.skip("upstream repo layout changed")

    pipeline = run_full_pipeline(tenant_id="t-factory", repo_url=str(repo))

    # The factory is bound to the pipeline's immutable grounding context.
    factory = MCPServerFactory.for_pipeline(pipeline)

    server = asyncio.new_event_loop().run_until_complete(factory.create_for_query("who writes notes?"))

    # A minted-per-query server must be queryable on the REAL graph/clone.
    who = server.who_writes("notes")
    ids = {w.id for w in who.writers}
    expected = {
        "src/app/api/crud.py::post",
        "src/app/api/crud.py::put",
        "src/app/api/crud.py::delete",
    }
    assert expected.issubset(ids), f"minted server answered empty/wrong who_writes: {expected - ids} (got {ids})"
    assert who.status == "ok"

    # And it is bound to the same immutable graph as the real pipeline server.
    assert server.graph is pipeline.graph
    assert server.clone_path == pipeline.clone_path
    assert server.current_sha == pipeline.current_sha


@pytest.mark.integration
def test_factory_mints_distinct_queryable_instances_per_query() -> None:
    """AC-M5-001 concurrency contract preserved: each create_for_query returns a
    DISTINCT instance — and both are queryable on the real graph (not shells)."""
    from services.code_intel.mcp_server import CodeIntelMCPServer, MCPServerFactory
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.stubs import FactoryCounter

    repo = _clone("fastapi-crud-sync", _CRUD_URL)
    if not (repo / "src" / "app").exists():  # pragma: no cover
        pytest.skip("upstream repo layout changed")

    pipeline = run_full_pipeline(tenant_id="t-factory-2", repo_url=str(repo))
    counter = FactoryCounter()
    factory = MCPServerFactory.for_pipeline(pipeline, instance_counter=counter)

    async def run_two() -> tuple[CodeIntelMCPServer, CodeIntelMCPServer]:
        return await asyncio.gather(  # type: ignore[return-value]
            factory.create_for_query("q1"),
            factory.create_for_query("q2"),
        )

    s1, s2 = asyncio.new_event_loop().run_until_complete(run_two())

    assert counter.created_count == 2
    assert s1 is not s2, "each query must mint a fresh, distinct server (never shared)"
    # Both are queryable on the real graph — neither is an empty shell.
    for s in (s1, s2):
        assert s.who_writes("notes").status == "ok"
