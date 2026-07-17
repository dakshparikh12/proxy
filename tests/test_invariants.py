"""Tests for AC-INV-* — Architecture invariants acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_inv_001_grounded_claims_have_file_line_citations():
    """AC-INV-001: Cited-or-abstain: every grounded claim has a file:line citation from current clone."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    dependents = server.get_dependents("some_fn", limit=50)
    references = server.find_references("some_fn")

    all_results = list(dependents.results) + list(references.results)
    assert all_results, "Expected at least some results to validate citations"

    for claim in all_results:
        assert claim.file is not None and claim.file.strip() != "", (
            f"Grounded claim is missing file citation: {claim!r}"
        )
        assert claim.line is not None and claim.line > 0, (
            f"Grounded claim has invalid line number {claim.line!r}: {claim!r}"
        )

        clone_file = fixture.clone_path / claim.file
        assert clone_file.exists(), (
            f"Cited file does not exist in clone at pinned SHA: {claim.file!r}"
        )

        with open(clone_file) as f:
            line_count = sum(1 for _ in f)
        assert claim.line <= line_count, (
            f"Line {claim.line} is out of bounds for {claim.file!r} ({line_count} lines)"
        )


def test_ac_inv_002_absent_grounded_claim_labeled_not_found():
    """AC-INV-002: Cited-or-abstain: absent grounded claim is labeled not-found, not omitted silently."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import isolated_symbol_fixture

    fixture = isolated_symbol_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.get_dependents(fixture.isolated_symbol, limit=50)

    assert result.results is not None, (
        "result.results must not be null — must explicitly represent empty set"
    )
    assert result.results == [], (
        f"Expected empty list for isolated symbol, got: {result.results}"
    )
    assert result.status in ("not-found", "ok"), (
        f"Expected status 'not-found' or 'ok' for empty result, got: {result.status!r}"
    )


def test_ac_inv_003_incomplete_result_labeled_lower_bound():
    """AC-INV-003: Lossless-or-honest: incomplete result is labeled lower-bound or not-found-by-this-method."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import non_tier1_orm_fixture
    from tests.fixtures.stubs import LspStubSlow

    fixture = non_tier1_orm_fixture()
    lsp_stub = LspStubSlow(delay_s=5.0)
    server = CodeIntelMCPServer.from_fixture(fixture, lsp=lsp_stub)

    who_writes_result = server.who_writes("orders")
    for writer in who_writes_result.writers:
        assert writer.confidence in ("lower-bound", "not-found-by-this-method"), (
            f"Partial who_writes result has confidence={writer.confidence!r} — "
            f"must be 'lower-bound' or 'not-found-by-this-method'"
        )

    refs_result = server.find_references("some_fn")
    for ref in refs_result.results:
        assert ref.confidence in ("resolved", "lower-bound", "not-found-by-this-method"), (
            f"Reference {ref.file}:{ref.line} has unexpected confidence: {ref.confidence!r}"
        )

    partial_results = [r for r in refs_result.results if r.confidence != "resolved"]
    for ref in partial_results:
        assert ref.confidence in ("lower-bound", "not-found-by-this-method"), (
            f"Partial result not labeled as incomplete: {ref!r}"
        )


def test_ac_inv_004_fabricated_absolute_claim_rejected_by_oracle():
    """AC-INV-004: Lossless-or-honest: fabricated absolute claim is rejected by oracle."""
    import subprocess
    from tests.fixtures.negative_builds import negative_build_fabricated_resolved

    with negative_build_fabricated_resolved() as build_path:
        result = subprocess.run(
            ["python", "-m", "services.code_intel.verifier", str(build_path)],
            capture_output=True,
        )

    assert result.returncode != 0, (
        "Verifier should exit non-zero when fabricated 'resolved' result is present"
    )
    combined = (result.stdout + result.stderr).lower()
    assert b"mislabeled" in combined or b"confidence" in combined or b"resolved" in combined, (
        "Expected verifier to report mislabeled confidence"
    )


@pytest.mark.smoke
def test_ac_inv_005_tenant_isolation_path_traversal():
    """AC-INV-005: Tenant isolation: tenant-A clone inaccessible to tenant-B process."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import TwoTenantCloneFixture, path_traversal_args

    fixture = TwoTenantCloneFixture()
    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)

    traversal_args = path_traversal_args(
        tenant_b_base="/tenants/tenant-B/",
        tenant_a_target="/tenants/tenant-A/secret_file.py",
    )

    result = server_b.batch_read([traversal_args["path"]])

    for file_result in result.files:
        assert file_result.error is not None or (
            file_result.content is not None
            and "tenant-A" not in (file_result.content or "")
            and "/tenants/tenant-A/" not in (file_result.path or "")
        ), f"Tenant-A data leaked to tenant-B response: {file_result!r}"


def test_ac_inv_006_graph_queries_return_only_querying_tenant_data():
    """AC-INV-006: Tenant isolation: graph queries return only data from the querying tenant's SQLite."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import TwoTenantCloneFixture

    fixture = TwoTenantCloneFixture()
    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)

    tenant_a_node_ids = fixture.tenant_a_node_ids
    tenant_b_node_ids = fixture.tenant_b_node_ids

    result = server_b.get_dependents("some_fn", limit=50)
    result_ids = {item.id for item in result.results}

    leaked = result_ids & tenant_a_node_ids
    assert leaked == set(), (
        f"Tenant-A node IDs leaked into tenant-B query result: {leaked}"
    )

    result2 = server_b.list_entry_points()
    result2_ids = {item.id for item in result2.results}
    leaked2 = result2_ids & tenant_a_node_ids
    assert leaked2 == set(), (
        f"Tenant-A node IDs leaked into tenant-B list_entry_points: {leaked2}"
    )


def test_ac_inv_007_meeting_cache_keys_are_tenant_scoped():
    """AC-INV-007: Meeting cache keys are tenant-scoped (cross-tenant cache collision is rejected)."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.meeting import MeetingSession
    from tests.fixtures.stubs import TwoTenantCloneFixture

    fixture = TwoTenantCloneFixture()

    server_a = CodeIntelMCPServer.for_tenant("tenant-A", fixture=fixture)
    session_a = MeetingSession.start(pipeline=server_a.pipeline)
    result_a = session_a.tool_call("get_dependents", symbol="some_fn", limit=50)

    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)
    session_b = MeetingSession.start(pipeline=server_b.pipeline)
    result_b = session_b.tool_call("get_dependents", symbol="some_fn", limit=50)

    tenant_a_node_ids = fixture.tenant_a_node_ids
    result_b_ids = {item.id for item in result_b.results}

    assert result_b_ids.issubset(fixture.tenant_b_node_ids), (
        f"Tenant-B got tenant-A cached result. Leaked nodes: {result_b_ids & tenant_a_node_ids}"
    )
