"""Tests for AC-M3-* — Exclusions milestone acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m3_001_gitleaks_runs_on_changed_files_after_clone():
    """AC-M3-001: Gitleaks runs on changed files after initial clone."""
    from services.code_intel.cloner import Cloner
    from services.code_intel.exclusions import ExclusionManager
    from tests.fixtures.stubs import PlantedSecretsRepo, GitleaksInstrumented

    fixture = PlantedSecretsRepo()
    gitleaks = GitleaksInstrumented()
    exclusions = ExclusionManager(gitleaks=gitleaks)
    cloner = Cloner(exclusion_manager=exclusions)

    clone_path = cloner.clone(tenant_id="tenant-test", repo_url=fixture.url)

    assert gitleaks.call_count >= 1, "Gitleaks was not invoked after clone"
    assert fixture.secret_file in exclusions.get_excluded_paths(clone_path), (
        f"Secret-bearing file {fixture.secret_file} not in exclusion set"
    )


@pytest.mark.smoke
def test_ac_m3_002_gitleaks_runs_after_every_delta_pull():
    """AC-M3-002: Gitleaks runs on changed files after every delta pull."""
    from services.code_intel.cloner import Cloner
    from services.code_intel.exclusions import ExclusionManager
    from tests.fixtures.stubs import PlantedSecretsRepo, GitleaksInstrumented, push_webhook_fixture
    from services.code_intel.webhook_handler import WebhookHandler

    fixture = PlantedSecretsRepo(secret_introduced_on_push=True)
    gitleaks = GitleaksInstrumented()
    exclusions = ExclusionManager(gitleaks=gitleaks)
    cloner = Cloner(exclusion_manager=exclusions)
    handler = WebhookHandler(cloner=cloner)

    clone_path = cloner.clone(tenant_id="tenant-test", repo_url=fixture.url)
    gitleaks.reset()

    webhook = push_webhook_fixture(repo_url=fixture.url, sha="newsha", changed_files=[fixture.new_secret_file])
    handler.handle(webhook)

    assert gitleaks.call_count >= 1, "Gitleaks was not invoked after delta pull"
    assert fixture.new_secret_file in exclusions.get_excluded_paths(clone_path), (
        f"New secret file {fixture.new_secret_file} not excluded after pull"
    )


def test_ac_m3_003_excluded_paths_absent_from_dependency_graph():
    """AC-M3-003: Excluded paths are absent from the dependency graph."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.graph_builder import GraphBuilder
    from tests.fixtures.stubs import PlantedSecretsRepo, PolicyGlobsFixture

    fixture = PlantedSecretsRepo()
    policy = PolicyGlobsFixture(globs=[".env*", "node_modules/"])

    result = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        policy_globs=policy.globs,
    )

    exclusion_set = result.exclusion_manager.get_excluded_paths(result.clone_path)
    graph = result.graph

    for excluded_path in exclusion_set:
        nodes_for_path = [n for n in graph.nodes if n.path == str(excluded_path)]
        assert nodes_for_path == [], (
            f"Excluded path {excluded_path} found in dependency graph: {nodes_for_path}"
        )


def test_ac_m3_004_excluded_paths_absent_from_tool_results():
    """AC-M3-004: Excluded paths are absent from all tool query results."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import PlantedSecretsRepo, PolicyGlobsFixture

    fixture = PlantedSecretsRepo()
    policy = PolicyGlobsFixture(globs=[".env*", "node_modules/"])

    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        policy_globs=policy.globs,
    )
    server = CodeIntelMCPServer(pipeline=pipeline)
    exclusion_set = pipeline.exclusion_manager.get_excluded_paths(pipeline.clone_path)

    dependents_result = server.get_dependents("some_symbol", limit=50)
    for item in dependents_result.results:
        assert item.path not in exclusion_set, f"Excluded path in get_dependents result: {item.path}"

    entry_points_result = server.list_entry_points()
    for item in entry_points_result.results:
        assert item.path not in exclusion_set, f"Excluded path in list_entry_points result: {item.path}"

    for excluded_path in list(exclusion_set)[:3]:
        batch_result = server.batch_read([str(excluded_path)])
        assert batch_result.files[0].error is not None, (
            f"batch_read of excluded path {excluded_path} should return error, not content"
        )
        assert batch_result.files[0].content is None


def test_ac_m3_005_excluded_paths_never_copied_into_sandbox():
    """AC-M3-005: Excluded paths are never copied into sandbox."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.sandbox import prepare_sandbox
    from tests.fixtures.stubs import PlantedSecretsRepo, PolicyGlobsFixture

    fixture = PlantedSecretsRepo()
    policy = PolicyGlobsFixture(globs=[".env*", "node_modules/"])

    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        policy_globs=policy.globs,
    )
    exclusion_set = pipeline.exclusion_manager.get_excluded_paths(pipeline.clone_path)

    sandbox = prepare_sandbox(pipeline=pipeline)

    sandbox_files = {str(p) for p in sandbox.file_list()}
    for excluded_path in exclusion_set:
        assert str(excluded_path) not in sandbox_files, (
            f"Excluded path {excluded_path} found in sandbox file list"
        )


@pytest.mark.smoke
def test_ac_m3_006_detected_secret_redacted_in_batch_read():
    """AC-M3-006: Detected secret values are redacted in batch_read output."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import SecretInNonExcludedFile

    sentinel = "AKIAIOSFODNN7EXAMPLE"
    fixture = SecretInNonExcludedFile(secret_value=sentinel)

    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)
    server = CodeIntelMCPServer(pipeline=pipeline)

    result = server.batch_read([str(fixture.file_path)])
    content = result.files[0].content

    assert sentinel not in content, "Sentinel secret value found unredacted in batch_read output"
    assert "[REDACTED]" in content, "Expected [REDACTED] marker in batch_read output"


def test_ac_m3_007_secret_redaction_applies_to_all_read_path_tools():
    """AC-M3-007: Secret redaction applies to all read-path tools, not only batch_read."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.stubs import SecretInNonExcludedFile

    sentinel = "AKIAIOSFODNN7EXAMPLE"
    fixture = SecretInNonExcludedFile(secret_value=sentinel)

    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)
    server = CodeIntelMCPServer(pipeline=pipeline)

    batch_result = server.batch_read([str(fixture.file_path)])
    assert sentinel not in (batch_result.files[0].content or ""), (
        "Sentinel found in batch_read output"
    )

    refs_result = server.find_references(fixture.symbol_in_file)
    for ref in refs_result.results:
        assert sentinel not in (ref.context or ""), (
            f"Sentinel found in find_references context: {ref.context}"
        )


def test_ac_m3_008_excluded_path_membership_not_logged():
    """AC-M3-008: Excluded path membership (secret content) not logged."""
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.stubs import PlantedSecretsRepo, LogCapture

    fixture = PlantedSecretsRepo(secret_value="SUPER_SECRET_VALUE_CANARY")
    capture = LogCapture()

    with capture:
        run_full_pipeline(
            tenant_id="tenant-test",
            repo_url=fixture.url,
        )

    assert fixture.secret_value not in capture.output, (
        f"Secret value appeared in log output"
    )
