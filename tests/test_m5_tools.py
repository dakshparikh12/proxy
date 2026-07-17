"""Tests for AC-M5-* — code_intel MCP tools acceptance criteria."""
import pytest


def test_ac_m5_001_mcp_server_minted_fresh_per_query():
    """AC-M5-001: MCP server is minted fresh per query from a factory (never shared)."""
    import asyncio
    from services.code_intel.mcp_server import MCPServerFactory
    from tests.fixtures.stubs import FactoryCounter

    counter = FactoryCounter()
    factory = MCPServerFactory(instance_counter=counter)

    async def run_two_concurrent():
        import asyncio
        results = await asyncio.gather(
            factory.create_for_query("query-1"),
            factory.create_for_query("query-2"),
        )
        return results

    server1, server2 = asyncio.get_event_loop().run_until_complete(run_two_concurrent())

    assert counter.created_count == 2, (
        f"Expected 2 server instances created, got {counter.created_count}"
    )
    assert server1 is not server2, "Server instances must be distinct (not shared)"


@pytest.mark.smoke
def test_ac_m5_002_get_dependents_transitive_reverse_deps():
    """AC-M5-002: get_dependents returns transitive reverse-dependency set over correct edge types."""
    import json
    from pathlib import Path
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import known_reference_graph_fixture

    fixture = known_reference_graph_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.get_dependents("F", limit=50)
    result_ids = {item.id for item in result.results}

    golden_path = Path("fixtures/goldens/fixture-known-reference-graph/dependents-F.json")
    golden = json.loads(golden_path.read_text())
    golden_ids = set(golden["expected_ids"])

    assert result_ids == golden_ids, (
        f"get_dependents result mismatch.\nExpected: {golden_ids}\nGot: {result_ids}"
    )
    assert result.truncated_count == 0


def test_ac_m5_003_get_dependents_does_not_traverse_reads_edges_transitively():
    """AC-M5-003: get_dependents does NOT traverse reads edges transitively (founder decision)."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import reads_edge_chain_fixture

    fixture = reads_edge_chain_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.get_dependents("D", limit=50)
    result_ids = {item.id for item in result.results}

    assert "C" in result_ids, "C (depth-1 reader of D) should be in get_dependents result"
    assert "B" not in result_ids, (
        "B should NOT be in result — reads edges must not be traversed beyond depth-1"
    )
    assert "A" not in result_ids, (
        "A should NOT be in result — reads edges must not be traversed beyond depth-1"
    )


def test_ac_m5_004_get_dependents_ranked_and_capped_with_truncated_count():
    """AC-M5-004: get_dependents result is ranked by PageRank and capped at limit with truncated_count."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import large_dependency_fan_fixture

    fixture = large_dependency_fan_fixture(total_dependents=60)
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.get_dependents("T", limit=50)

    assert len(result.results) == 50, (
        f"Expected 50 results (capped), got {len(result.results)}"
    )
    assert result.truncated_count == 10, (
        f"Expected truncated_count=10, got {result.truncated_count}"
    )

    scores = [item.pagerank for item in result.results]
    assert scores == sorted(scores, reverse=True), (
        "Results are not sorted by descending PageRank"
    )


@pytest.mark.smoke
def test_ac_m5_005_who_writes_resolved_on_tier1_orm():
    """AC-M5-005: who_writes returns 'resolved' tag on tier-1 ORM stacks."""
    import json
    from pathlib import Path
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import django_model_fixture

    fixture = django_model_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.who_writes("orders")

    golden_path = Path("fixtures/goldens/fixture-django-model/who-writes-orders.json")
    golden = json.loads(golden_path.read_text())
    golden_writers = {w["id"] for w in golden["writers"]}

    result_ids = {w.id for w in result.writers}
    assert result_ids == golden_writers, (
        f"who_writes writer set mismatch. Expected: {golden_writers}, Got: {result_ids}"
    )

    for writer in result.writers:
        assert writer.confidence == "resolved", (
            f"Writer {writer.id} has confidence={writer.confidence!r}, expected 'resolved'"
        )


def test_ac_m5_006_who_writes_lower_bound_on_non_tier1_orm():
    """AC-M5-006: who_writes returns 'lower-bound' tag on non-tier-1 stacks (never silent exact)."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import non_tier1_orm_fixture

    fixture = non_tier1_orm_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.who_writes("orders")

    for writer in result.writers:
        assert writer.confidence == "lower-bound", (
            f"Writer {writer.id} has confidence={writer.confidence!r} on non-tier-1 ORM, "
            f"expected 'lower-bound'"
        )

    resolved_writers = [w for w in result.writers if w.confidence == "resolved"]
    assert resolved_writers == [], (
        f"No 'resolved' writers allowed on non-tier-1 ORM, got: {resolved_writers}"
    )


def test_ac_m5_007_shares_table_returns_co_accessing_modules():
    """AC-M5-007: shares_table returns independent modules co-accessing table with correct confidence tier."""
    import json
    from pathlib import Path
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import cross_module_table_access_fixture

    fixture = cross_module_table_access_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.shares_table("payments")

    golden_path = Path("fixtures/goldens/fixture-cross-module-table-access/shares-table-payments.json")
    golden = json.loads(golden_path.read_text())

    result_ids = {m.id for m in result.modules}
    golden_ids = set(golden["expected_module_ids"])
    assert result_ids == golden_ids, (
        f"shares_table modules mismatch. Expected: {golden_ids}, Got: {result_ids}"
    )

    for module in result.modules:
        assert module.confidence in ("resolved", "lower-bound"), (
            f"Module {module.id} has unexpected confidence: {module.confidence!r}"
        )


@pytest.mark.smoke
def test_ac_m5_008_list_entry_points_returns_zero_in_degree_nodes():
    """AC-M5-008: list_entry_points returns nodes with zero in-degree."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import known_reference_graph_fixture

    fixture = known_reference_graph_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.list_entry_points()
    result_ids = {item.id for item in result.results}

    assert "A" in result_ids, "A (zero in-degree) should be in list_entry_points result"
    assert "B" not in result_ids, "B (has incoming edge from C) should NOT be in result"


def test_ac_m5_009_owner_returns_codeowners_result_tagged_resolved():
    """AC-M5-009: owner returns CODEOWNERS result tagged 'resolved'."""
    import json
    from pathlib import Path
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import codeowners_fixture

    fixture = codeowners_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.owner(fixture.owned_path)

    golden_path = Path("fixtures/goldens/fixture-codeowners/owner.json")
    golden = json.loads(golden_path.read_text())

    assert result.owner == golden["owner"], (
        f"Owner mismatch. Expected: {golden['owner']}, Got: {result.owner}"
    )
    assert result.confidence == "resolved", (
        f"Expected confidence='resolved' for CODEOWNERS match, got: {result.confidence!r}"
    )


def test_ac_m5_010_owner_falls_back_to_git_blame_lower_bound():
    """AC-M5-010: owner falls back to git-blame tagged 'lower-bound' when no CODEOWNERS match."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import no_codeowners_fixture

    fixture = no_codeowners_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.owner(fixture.any_path)

    assert result is not None, "owner() should return a result (git-blame fallback), not None"
    assert result.confidence == "lower-bound", (
        f"Expected confidence='lower-bound' for git-blame fallback, got: {result.confidence!r}"
    )
    assert result.confidence != "resolved", (
        "No result should be tagged 'resolved' when no CODEOWNERS match"
    )


def test_ac_m5_011_batch_read_parallel_partial_failure():
    """AC-M5-011: batch_read reads up to 10 files in parallel and returns partial results on per-file failure."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import ConcurrencyInstrumented

    fixture = small_repo_fixture()
    concurrency = ConcurrencyInstrumented()
    server = CodeIntelMCPServer.from_fixture(fixture, concurrency=concurrency)

    valid_paths = [str(p) for p in fixture.all_files[:10]]
    invalid_path = "/nonexistent/path/does_not_exist.py"
    paths = valid_paths + [invalid_path]

    result = server.batch_read(paths, max_lines_per_file=100)

    successes = [f for f in result.files if f.error is None]
    errors = [f for f in result.files if f.error is not None]

    assert len(successes) == 10, f"Expected 10 successes, got {len(successes)}"
    assert len(errors) == 1, f"Expected 1 error entry, got {len(errors)}"
    assert errors[0].path == invalid_path

    assert concurrency.max_concurrent >= 2, (
        "Expected parallel file reads (max_concurrent should be > 1)"
    )


def test_ac_m5_012_batch_read_capped_at_10_files():
    """AC-M5-012: batch_read cap at 10 files per call (rejects > 10 or truncates with signal)."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    paths_15 = [f"/repo/file_{i}.py" for i in range(15)]
    result = server.batch_read(paths_15)

    assert len(result.files) <= 10, (
        f"batch_read returned {len(result.files)} files, expected <= 10"
    )

    if len(result.files) < len(paths_15):
        assert result.truncated or result.truncated_count is not None, (
            "Truncation was silent — must be signaled when > 10 files requested"
        )


def test_ac_m5_013_lookup_referent_deterministic_zero_llm():
    """AC-M5-013: lookup_referent resolves deterministically to node_id, area, or None with zero LLM calls."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import LLMCallCounter
    from tests.fixtures.repos import known_reference_graph_fixture

    fixture = known_reference_graph_fixture()
    counter = LLMCallCounter()
    server = CodeIntelMCPServer.from_fixture(fixture, llm_counter=counter)

    result_1 = server.lookup_referent("some_symbol")
    result_2 = server.lookup_referent("some_symbol")

    assert result_1 == result_2, (
        f"lookup_referent is non-deterministic: {result_1!r} != {result_2!r}"
    )
    assert counter.call_count == 0, (
        f"lookup_referent made {counter.call_count} LLM calls, expected 0"
    )


def test_ac_m5_014_per_meeting_cache_returns_stored_result():
    """AC-M5-014: Per-meeting cache returns stored result for repeat call with identical args."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.meeting import MeetingSession
    from tests.fixtures.stubs import DBQueryCounter
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    db_counter = DBQueryCounter()
    server = CodeIntelMCPServer.from_fixture(fixture, db_counter=db_counter)

    session = MeetingSession(server=server)

    result_1 = session.tool_call("get_dependents", symbol="some_fn", limit=50)
    db_counter.reset()
    result_2 = session.tool_call("get_dependents", symbol="some_fn", limit=50)

    assert result_1 == result_2, "Cached result differs from original"
    assert db_counter.query_count == 0, (
        f"Second call hit DB ({db_counter.query_count} queries) — expected 0 (cached)"
    )


def test_ac_m5_015_cache_invalidated_on_push_webhook():
    """AC-M5-015: Cache is invalidated on push webhook for the affected repo's meetings."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import DBQueryCounter, push_webhook_fixture
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    db_counter = DBQueryCounter()
    server = CodeIntelMCPServer.from_fixture(fixture, db_counter=db_counter)
    handler = WebhookHandler(server=server)

    session = MeetingSession(server=server)
    session.tool_call("get_dependents", symbol="some_fn", limit=50)

    webhook = push_webhook_fixture(repo_url=fixture.url, sha="newsha")
    handler.handle(webhook)

    db_counter.reset()
    session.tool_call("get_dependents", symbol="some_fn", limit=50)

    assert db_counter.query_count > 0, (
        "Expected DB re-query after push (cache should be invalidated), got 0 queries"
    )
