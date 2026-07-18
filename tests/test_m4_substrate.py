"""Tests for AC-M4-* — Structural substrate / dependency graph acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m4_001_every_source_file_parsed_by_tree_sitter():
    """AC-M4-001: Every non-excluded source file is parsed by tree-sitter to extract declarations."""
    import json
    from pathlib import Path
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    golden_path = Path("fixtures/goldens/fixture-small-repo/declarations.json")
    golden = json.loads(golden_path.read_text())

    coverage = pipeline.coverage_record
    non_excluded = [f for f in fixture.all_files if f not in pipeline.exclusion_set]
    for f in non_excluded:
        assert coverage.has_entry(f), f"No coverage entry for non-excluded file: {f}"

    graph_node_ids = {n.id for n in pipeline.graph.nodes}
    for decl in golden["declarations"]:
        assert decl["id"] in graph_node_ids, (
            f"Golden declaration {decl['id']} missing from graph"
        )

    total = len(non_excluded)
    covered = sum(1 for f in non_excluded if coverage.has_entry(f))
    assert covered == total, f"Declaration recall < 1.0: {covered}/{total} files covered"


def test_ac_m4_002_symbols_ranked_by_pagerank():
    """AC-M4-002: Symbols ranked by PageRank over the tag-reference graph."""
    import json
    from pathlib import Path
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import known_reference_graph_fixture

    fixture = known_reference_graph_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    golden_path = Path("fixtures/goldens/fixture-known-reference-graph/pagerank.json")
    golden = json.loads(golden_path.read_text())
    golden_top5 = [entry["id"] for entry in golden["top5"]]

    ranked_nodes = pipeline.graph.get_nodes_by_pagerank(limit=5)
    actual_top5 = [n.id for n in ranked_nodes]

    assert actual_top5 == golden_top5, (
        f"PageRank top-5 ordering mismatch.\nExpected: {golden_top5}\nGot: {actual_top5}"
    )


def test_ac_m4_003_pagerank_scores_non_null_non_negative():
    """AC-M4-003: PageRank scores are non-null and non-negative for all indexed nodes."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    for node in pipeline.graph.nodes:
        assert node.pagerank is not None, f"Node {node.id} has null pagerank"
        assert node.pagerank >= 0.0, f"Node {node.id} has negative pagerank: {node.pagerank}"


@pytest.mark.smoke
def test_ac_m4_004_zero_llm_calls_during_graph_build():
    """AC-M4-004: Zero LLM calls during structural substrate and graph build."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.stubs import LLMCallCounter
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    counter = LLMCallCounter()

    run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        llm_call_counter=counter,
    )

    assert counter.call_count == 0, (
        f"Expected 0 LLM calls during graph build, got {counter.call_count}"
    )


def test_ac_m4_005_llm_call_in_graph_build_rejected_by_verifier():
    """AC-M4-005: LLM call in graph build is rejected by verifier (pre-build negative)."""
    import subprocess
    from tests.fixtures.negative_builds import negative_build_llm_in_graph

    with negative_build_llm_in_graph() as build_path:
        result = subprocess.run(
            ["python", "-m", "services.code_intel.verifier", str(build_path)],
            capture_output=True,
        )

    assert result.returncode != 0, "Verifier should exit non-zero on LLM call in graph build"
    combined = (result.stdout + result.stderr).lower()
    assert b"llm" in combined or b"model" in combined, (
        "Expected verifier to report LLM violation"
    )


def test_ac_m4_006_file_coverage_accounts_for_every_tracked_file():
    """AC-M4-006: File-coverage record accounts for every tracked file (indexed + flagged == git ls-files)."""
    import subprocess
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=pipeline.clone_path,
        capture_output=True,
        text=True,
    )
    git_files = set(result.stdout.strip().splitlines())

    coverage = pipeline.coverage_record
    indexed_count = coverage.count_by_status("indexed")
    flagged_count = coverage.count_by_status("flagged")

    assert indexed_count + flagged_count == len(git_files), (
        f"Coverage mismatch: indexed({indexed_count}) + flagged({flagged_count}) "
        f"!= git ls-files({len(git_files)})"
    )


def test_ac_m4_007_each_coverage_row_has_exactly_one_valid_status():
    """AC-M4-007: Each coverage record row has exactly one of: status=indexed or status=flagged."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    valid_statuses = {"indexed", "flagged"}
    for row in pipeline.coverage_record.all_rows():
        assert row.status in valid_statuses, (
            f"Coverage row for {row.path} has invalid status: {row.status!r}"
        )
        if row.status == "flagged":
            assert row.flag_reason and row.flag_reason.strip(), (
                f"Flagged row for {row.path} has empty flag_reason"
            )


@pytest.mark.smoke
def test_ac_m4_008_table_nodes_use_canonical_id_format():
    """AC-M4-008: Table nodes use canonical id 'table::<name>' and kind=='table'."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import django_model_fixture

    fixture = django_model_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    table_nodes = [n for n in pipeline.graph.nodes if n.kind == "table"]
    assert table_nodes, "No table nodes found in graph"

    for node in table_nodes:
        assert node.id.startswith("table::"), (
            f"Table node id does not start with 'table::': {node.id!r}"
        )
        assert node.kind == "table", (
            f"Table node kind is not exactly 'table': {node.kind!r}"
        )

    order_nodes = [n for n in table_nodes if "Order" in n.id]
    assert any(n.id == "table::Order" for n in order_nodes), (
        f"Expected node id 'table::Order', found: {[n.id for n in order_nodes]}"
    )


def test_ac_m4_009_push_triggers_full_graph_rebuild():
    """AC-M4-009: Push event triggers full graph rebuild (drop + re-extract), not incremental."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import DBOperationCounter, push_webhook_fixture
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    db_counter = DBOperationCounter()
    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        db_operation_counter=db_counter,
    )

    db_counter.reset()
    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="newsha")
    handler.handle(webhook)

    ops = db_counter.recorded_operations
    drop_idx = next(
        (i for i, op in enumerate(ops) if op.type in ("DROP", "DELETE_ALL", "TRUNCATE")),
        None,
    )
    insert_idx = next(
        (i for i, op in enumerate(ops) if op.type == "INSERT"),
        None,
    )

    assert drop_idx is not None, "No bulk-delete/DROP recorded before re-extraction"
    assert insert_idx is not None, "No INSERT recorded after bulk-delete"
    assert drop_idx < insert_idx, "DROP must occur before INSERT in full rebuild"


def test_ac_m4_010_grammarless_language_files_flagged_unsupported():
    """AC-M4-010: Grammarless-language files are flagged 'unsupported-language' and still live-searchable."""
    import subprocess
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import unsupported_language_file_fixture

    fixture = unsupported_language_file_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    coverage_row = pipeline.coverage_record.get(fixture.unsupported_file)
    assert coverage_row is not None, f"No coverage row for unsupported file {fixture.unsupported_file}"
    assert coverage_row.status == "flagged", (
        f"Expected status='flagged' for unsupported file, got: {coverage_row.status!r}"
    )
    assert coverage_row.flag_reason == "unsupported-language", (
        f"Expected flag_reason='unsupported-language', got: {coverage_row.flag_reason!r}"
    )

    graph_nodes_for_file = [n for n in pipeline.graph.nodes if n.path == fixture.unsupported_file]
    assert graph_nodes_for_file == [], (
        f"Unsupported-language file should not appear as graph node: {graph_nodes_for_file}"
    )

    rg_result = subprocess.run(
        ["rg", fixture.unique_content, str(pipeline.clone_path)],
        capture_output=True,
        text=True,
    )
    assert rg_result.returncode == 0, (
        f"ripgrep could not find content in unsupported-language file (should be live-searchable)"
    )


def test_ac_m4_011_broken_supported_file_flagged_and_searchable():
    """AC-M4-011: A broken/mid-edit supported-language file is flagged in coverage
    and still reachable via live search (ripgrep finds its content)."""
    import subprocess
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import parse_error_fixture

    fixture = parse_error_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    coverage = pipeline.coverage_record
    broken_row = coverage.get(fixture.broken_file)
    assert broken_row is not None, (
        f"No coverage entry for broken file {fixture.broken_file}"
    )
    assert broken_row.status == "flagged", (
        f"Expected broken file to be flagged, got: {broken_row.status!r}"
    )

    rg_result = subprocess.run(
        ["rg", fixture.unique_content, str(pipeline.clone_path)],
        capture_output=True,
        text=True,
    )
    assert rg_result.returncode == 0, (
        "ripgrep could not find content in broken file (should be live-searchable)"
    )


def test_ac_m4_012_ranked_overview_bounded_by_limit():
    """AC-M4-012: The ranked overview (get_nodes_by_pagerank) respects a limit parameter,
    serving as the token-budgeting mechanism for overview output."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    all_nodes = pipeline.graph.get_nodes_by_pagerank()
    limited = pipeline.graph.get_nodes_by_pagerank(limit=3)

    assert len(limited) <= 3, (
        f"get_nodes_by_pagerank(limit=3) returned {len(limited)} nodes, expected <= 3"
    )
    assert len(limited) <= len(all_nodes), (
        "Limited query returned more nodes than unlimited query"
    )
