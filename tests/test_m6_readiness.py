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
