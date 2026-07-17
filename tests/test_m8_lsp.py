"""Tests for AC-M8-* — Precise navigation / LSP acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m8_001_find_references_resolved_from_lsp():
    """AC-M8-001: find_references returns exact results tagged 'resolved' from LSP when available."""
    import json
    from pathlib import Path
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import LspStubWarm

    fixture = small_repo_fixture()
    lsp_stub = LspStubWarm()
    server = CodeIntelMCPServer.from_fixture(fixture, lsp=lsp_stub)

    result = server.find_references(fixture.known_symbol)

    golden_path = Path("fixtures/goldens/fixture-small-repo/find-references.json")
    golden = json.loads(golden_path.read_text())
    golden_refs = {(r["file"], r["line"]) for r in golden["references"]}

    result_refs = {(r.file, r.line) for r in result.results}
    assert result_refs == golden_refs, (
        f"find_references result mismatch.\nExpected: {golden_refs}\nGot: {result_refs}"
    )

    for ref in result.results:
        assert ref.confidence == "resolved", (
            f"Reference {ref.file}:{ref.line} has confidence={ref.confidence!r}, expected 'resolved'"
        )


def test_ac_m8_002_lsp_timeout_triggers_grep_fallback_lower_bound():
    """AC-M8-002: LSP timeout triggers grep fallback tagged 'lower-bound'."""
    import time
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import LspStubSlow

    fixture = small_repo_fixture()
    lsp_stub = LspStubSlow(delay_s=5.0)
    server = CodeIntelMCPServer.from_fixture(fixture, lsp=lsp_stub)

    start = time.monotonic()
    result = server.find_references(fixture.known_symbol)
    elapsed = time.monotonic() - start

    assert elapsed < 4.0, (
        f"find_references took {elapsed:.2f}s — expected completion within 4s on LSP timeout"
    )
    assert result.results, "Expected grep fallback results, got empty list"
    for ref in result.results:
        assert ref.confidence == "lower-bound", (
            f"Fallback reference has confidence={ref.confidence!r}, expected 'lower-bound'"
        )


def test_ac_m8_003_hung_lsp_server_restarted_after_timeout():
    """AC-M8-003: Hung LSP server is restarted after timeout."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import LspStubSlow, LspLifecycleInstrumented

    fixture = small_repo_fixture()
    lsp_stub = LspStubSlow(delay_s=5.0)
    lifecycle = LspLifecycleInstrumented(lsp_stub)
    server = CodeIntelMCPServer.from_fixture(fixture, lsp=lifecycle)

    server.find_references(fixture.known_symbol)

    assert lifecycle.restart_count >= 1, (
        "Expected LSP server to be restarted after timeout, got 0 restarts"
    )

    result2 = server.find_references(fixture.known_symbol)
    assert result2 is not None, "Second find_references call failed after LSP restart"


@pytest.mark.smoke
def test_ac_m8_004_lsp_fallback_tagged_lower_bound_not_silent_or_stale():
    """AC-M8-004: LSP fallback returns 'lower-bound', never silently drops or returns stale LSP results."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import LspStubSlow

    fixture = small_repo_fixture()
    lsp_stub = LspStubSlow(delay_s=5.0)
    server = CodeIntelMCPServer.from_fixture(fixture, lsp=lsp_stub)

    result = server.find_references(fixture.known_symbol)

    assert result.results, (
        "Fallback result is empty — results must not be silently dropped on LSP timeout"
    )
    for ref in result.results:
        assert ref.confidence == "lower-bound", (
            f"Fallback reference {ref.file}:{ref.line} has confidence={ref.confidence!r}, "
            f"expected 'lower-bound'"
        )

    assert not getattr(result, "_from_stale_lsp_cache", False), (
        "Returned stale LSP results from a previous successful call — must use grep fallback"
    )
