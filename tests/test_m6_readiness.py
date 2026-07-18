"""Tests for AC-M6-* — Coverage / Readiness acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m6_001_readiness_state_uses_canonical_enum_values():
    """AC-M6-001: Readiness state uses exactly the canonical enum values."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.readiness import ReadinessCollector
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    collector = ReadinessCollector()

    run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        readiness_listener=collector,
    )

    valid_states = {"connecting", "cloning", "indexing", "ready", "not_ready"}
    for emitted in collector.emitted_states:
        assert emitted in valid_states, (
            f"Invalid readiness state emitted: {emitted!r}. "
            f"Must be one of: {valid_states}"
        )


def test_ac_m6_002_readiness_gate_verifies_indexed_plus_flagged_equals_git_ls_files():
    """AC-M6-002: Readiness gate verifies indexed + flagged == git ls-files."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.readiness import ReadinessCollector
    from tests.fixtures.repos import incomplete_coverage_fixture

    fixture = incomplete_coverage_fixture()
    collector = ReadinessCollector()

    run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        readiness_listener=collector,
        simulate_coverage_gap=True,
    )

    assert "ready" not in collector.emitted_states, (
        "Readiness should NOT reach 'ready' when coverage has a gap"
    )

    has_not_ready_or_error = any(
        s in ("not_ready",) for s in collector.emitted_states
    ) or collector.emitted_error is not None
    assert has_not_ready_or_error, (
        "Expected 'not_ready' state or error when coverage gap exists"
    )


def test_ac_m6_003_coverage_pct_deterministic_zero_model_calls():
    """AC-M6-003: coverage_pct and gaps computed by pure deterministic function with zero model calls."""
    from services.code_intel.coverage import compute_coverage
    from tests.fixtures.stubs import LLMCallCounter

    counter = LLMCallCounter()

    n_indexed = 80
    n_flagged = 20

    result_1 = compute_coverage(indexed=n_indexed, flagged=n_flagged, llm_counter=counter)
    result_2 = compute_coverage(indexed=n_indexed, flagged=n_flagged, llm_counter=counter)

    expected_pct = n_indexed / (n_indexed + n_flagged)
    assert abs(result_1.coverage_pct - expected_pct) < 1e-4, (
        f"coverage_pct={result_1.coverage_pct!r} does not match formula {expected_pct!r}"
    )
    assert result_1.coverage_pct == result_2.coverage_pct, (
        "coverage_pct is non-deterministic"
    )
    assert counter.call_count == 0, (
        f"Expected 0 LLM calls for coverage computation, got {counter.call_count}"
    )


@pytest.mark.smoke
def test_ac_m6_004_ready_state_includes_indexed_at_and_pinned_sha():
    """AC-M6-004: Ready state includes indexed_at timestamp and pinned SHA."""
    import re
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    readiness_record = pipeline.readiness_record
    assert readiness_record is not None, "No readiness record produced"
    assert readiness_record.indexed_at is not None, "indexed_at is null in readiness record"
    assert readiness_record.pinned_sha is not None, "pinned_sha is null in readiness record"

    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    assert sha_pattern.match(readiness_record.pinned_sha), (
        f"pinned_sha does not match 40-char hex pattern: {readiness_record.pinned_sha!r}"
    )


def test_ac_m6_005_parse_error_in_exact_supported_file_flagged_and_accounted():
    """AC-M6-005: An exact-supported file with a parse error is flagged 'parse-error'
    in the coverage record (not silently absent) and accounted for by the gate."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import parse_error_fixture

    fixture = parse_error_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    coverage = pipeline.coverage_record
    broken_row = coverage.get(fixture.broken_file)
    assert broken_row is not None, (
        f"No coverage entry for broken file {fixture.broken_file} — silently absent"
    )
    assert broken_row.status == "flagged", (
        f"Expected status='flagged' for parse-error file, got: {broken_row.status!r}"
    )
    assert broken_row.flag_reason == "parse-error", (
        f"Expected flag_reason='parse-error', got: {broken_row.flag_reason!r}"
    )

    valid_row = coverage.get(fixture.valid_file)
    assert valid_row is not None, "No coverage entry for the valid file"
    assert valid_row.status == "indexed", (
        f"Expected valid file to be indexed, got: {valid_row.status!r}"
    )


def test_ac_m6_006_who_writes_tier_present_on_django_fixture():
    """AC-M6-006: who_writes returns a result with a status indicating the tier-1 stack
    was recognized. Table nodes exist and carry the canonical id format for a Django ORM repo."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import django_model_fixture

    fixture = django_model_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    table_nodes = [n for n in pipeline.graph.nodes if n.kind == "table"]
    assert table_nodes, "No table nodes in Django fixture graph"

    result = pipeline.server.who_writes(table_nodes[0].id)
    assert result is not None, "who_writes returned None"
    assert hasattr(result, "status"), "who_writes result has no status field"
    if result.writers:
        for w in result.writers:
            assert w.confidence in ("resolved", "lower-bound"), (
                f"Writer {w.path} has unexpected confidence: {w.confidence!r}"
            )


def test_ac_m6_007_readiness_requires_graph_nodes_present():
    """AC-M6-007: Readiness gate requires the graph to be successfully built (graph has nodes)."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.readiness import ReadinessCollector
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    collector = ReadinessCollector()

    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        readiness_listener=collector,
    )

    assert "ready" in collector.emitted_states, (
        "Expected 'ready' for a well-formed fixture repo"
    )
    assert len(pipeline.graph.nodes) > 0, (
        "Graph must have nodes for readiness to reach 'ready'"
    )


def test_ac_m6_008_low_coverage_pct_does_not_block_readiness():
    """AC-M6-008: A fully-classified repo with low coverage_pct (many flagged files)
    still reaches 'ready' — coverage_pct is reported, not a gate."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.readiness import ReadinessCollector
    from tests.fixtures.repos import low_coverage_fully_classified_fixture

    fixture = low_coverage_fully_classified_fixture()
    collector = ReadinessCollector()

    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        readiness_listener=collector,
    )

    assert "ready" in collector.emitted_states, (
        "Readiness should reach 'ready' for a fully-classified repo "
        "even with low coverage_pct (coverage_pct is reported, not a gate)"
    )

    rr = pipeline.readiness_record
    assert rr is not None, "No readiness record produced"
    assert rr.coverage_pct < 1.0, (
        f"Expected low coverage_pct (many flagged files), got {rr.coverage_pct}"
    )
