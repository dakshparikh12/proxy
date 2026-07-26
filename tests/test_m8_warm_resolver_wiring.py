"""Real-path wiring proof for node codeintel.precise_nav (AC-M8-*).

The AC-M8 stub tests inject an ``lsp=`` into ``CodeIntelMCPServer.from_fixture``.
Those prove the *seam*. This module proves the *live wiring*: that
``run_full_pipeline`` — the real production build — actually constructs the warm
host-side resolver (``warm_resolver.MultiLangResolver``, the §2.1 v0 seam) and
binds it onto the pipeline so ``find_references`` served through the real
per-query server factory returns ``resolved`` refs, not a permanent grep
lower-bound.

Without the wiring, ``pipeline.lsp`` is ``None`` and every ``find_references`` on
the production path is silently down-graded to ``lower-bound`` — the precision
instrument is dead. These tests fail-closed on that regression.
"""
from __future__ import annotations


def test_run_full_pipeline_binds_warm_resolver() -> None:
    """run_full_pipeline sets pipeline.lsp = the warm host-side resolver (§2.1)."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.warm_resolver import MultiLangResolver
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    assert getattr(pipeline, "lsp", None) is not None, (
        "run_full_pipeline did not bind a warm resolver to pipeline.lsp — "
        "find_references would be a permanent grep lower-bound on the real path"
    )
    assert isinstance(pipeline.lsp, MultiLangResolver), (
        f"pipeline.lsp is {type(pipeline.lsp).__name__}, expected the MultiLangResolver warm seam"
    )
    # The warm index is pre-built (prepare-ahead), so the known symbol resolves.
    assert pipeline.lsp.references(fixture.known_symbol), (
        "warm resolver did not pre-index the known symbol's definition site"
    )


def test_find_references_resolved_on_real_pipeline_path() -> None:
    """find_references through the real per-query factory returns 'resolved' refs
    (warm resolver answers within timeout), never a permanent lower-bound."""
    import asyncio

    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    # Mint a fresh, queryable server exactly as the wake turn does (§3.5 factory).
    server = asyncio.run(pipeline.server_factory.create_for_query(fixture.known_symbol))
    result = server.find_references(fixture.known_symbol)

    assert result.status == "ok", "find_references returned not-found on the real path"
    assert result.results, "find_references dropped all references on the real path"
    assert all(r.confidence == "resolved" for r in result.results), (
        "real-path find_references returned "
        f"{sorted({r.confidence for r in result.results})} — expected all 'resolved' "
        "from the warm resolver (grep-only lower-bound means the resolver was not wired)"
    )


def test_pipeline_bound_server_also_resolves() -> None:
    """The pipeline's own bound server (pipeline.server) is wired to the warm
    resolver too, not only the per-query factory."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    result = pipeline.server.find_references(fixture.known_symbol)
    assert result.results and all(r.confidence == "resolved" for r in result.results), (
        "pipeline.server.find_references not served 'resolved' from the warm resolver"
    )
