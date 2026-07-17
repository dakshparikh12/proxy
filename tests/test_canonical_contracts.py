"""Tests for AC-CANON-* — Canonical contracts acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_canon_001_code_intel_lives_under_services_not_libs():
    """AC-CANON-001: All code-intel code lives under services/code_intel, not libs/code_intel."""
    import glob as glob_module

    matches = glob_module.glob("libs/code_intel/**/*.py", recursive=True)
    assert matches == [], (
        f"Python modules found under libs/code_intel/ (must live under services/code_intel/): {matches}"
    )


@pytest.mark.smoke
def test_ac_canon_002_v0_search_uses_ripgrep_only():
    """AC-CANON-002: V0 search uses ripgrep only — no Zoekt or other backend."""
    from services.code_intel.verifier import StaticAnalysisVerifier

    verifier = StaticAnalysisVerifier()

    forbidden_backends = ["zoekt", "elasticsearch", "elastic_search", "whoosh", "sphinx_search"]
    for backend in forbidden_backends:
        import_violations = verifier.find_imports_of(backend)
        assert import_violations == [], (
            f"Forbidden search backend import '{backend}' found: {import_violations}"
        )
        call_violations = verifier.find_subprocess_calls_with(backend)
        assert call_violations == [], (
            f"Forbidden search backend subprocess call '{backend}' found: {call_violations}"
        )

    rg_calls = verifier.find_subprocess_calls_with("rg")
    text_search_calls = verifier.find_all_text_search_calls()
    non_rg_text_search = [c for c in text_search_calls if "rg" not in c.binary]
    assert non_rg_text_search == [], (
        f"Text search calls using non-rg binary: {non_rg_text_search}"
    )


@pytest.mark.smoke
def test_ac_canon_003_dependency_graph_stored_in_sqlite_not_postgres():
    """AC-CANON-003: Dependency graph is stored in per-repo SQLite, not Postgres or SHA-versioned tables."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.verifier import StaticAnalysisVerifier
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import DBConnectionTracer

    fixture = small_repo_fixture()
    tracer = DBConnectionTracer()

    run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        db_tracer=tracer,
    )

    for conn in tracer.opened_connections:
        assert conn.type == "sqlite3", (
            f"Graph data write used non-SQLite connection: {conn.type!r} at {conn.dsn!r}"
        )
        assert conn.path.endswith(".db"), (
            f"SQLite file does not end in .db: {conn.path!r}"
        )

    verifier = StaticAnalysisVerifier()
    postgres_imports = verifier.find_imports_of("psycopg2") + verifier.find_imports_of("asyncpg")
    graph_module_path = "services/code_intel"
    postgres_in_graph = [
        v for v in postgres_imports
        if graph_module_path in v.file
    ]
    assert postgres_in_graph == [], (
        f"Postgres imports found in code-intel graph layer: {postgres_in_graph}"
    )

    sha_table_violations = verifier.find_sha_versioned_table_schema()
    assert sha_table_violations == [], (
        f"SHA-versioned table schema found: {sha_table_violations}"
    )


def test_ac_canon_004_large_repo_lsp_kept_warm_after_connect_push():
    """AC-CANON-004: Large repo (>500k LOC) has language server kept warm after connect/push."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import LargeLOCStub, LspLifecycleInstrumented, push_webhook_fixture
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    loc_stub = LargeLOCStub(loc_count=600_000)
    lifecycle = LspLifecycleInstrumented()

    pipeline = run_full_pipeline(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        loc_provider=loc_stub,
        lsp_lifecycle=lifecycle,
    )

    assert lifecycle.is_running_after_connect, (
        "LSP server should remain warm (running) after connect on large repo (>500k LOC)"
    )

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="newsha")
    handler.handle(webhook)

    assert lifecycle.is_running_after_push, (
        "LSP server should remain warm (running) after push on large repo (>500k LOC)"
    )


def test_ac_canon_005_uninstall_triggers_hard_delete_within_15_minutes():
    """AC-CANON-005: Uninstall triggers hard-delete of clone, index, graph, and snapshots within 15 minutes."""
    import time
    from pathlib import Path
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import uninstall_webhook_fixture
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    clone_dir = pipeline.clone_path
    graph_db = pipeline.graph_db_path
    coverage_db = pipeline.coverage_db_path

    assert clone_dir.exists(), "Clone dir must exist before uninstall"
    assert graph_db.exists(), "Graph DB must exist before uninstall"
    assert coverage_db.exists(), "Coverage DB must exist before uninstall"

    handler = WebhookHandler(pipeline=pipeline)
    webhook = uninstall_webhook_fixture(tenant_id="tenant-test")

    start = time.monotonic()
    handler.handle(webhook)
    elapsed = time.monotonic() - start

    assert not clone_dir.exists(), f"Clone dir still exists after uninstall: {clone_dir}"
    assert not graph_db.exists(), f"Graph DB still exists after uninstall: {graph_db}"
    assert not coverage_db.exists(), f"Coverage DB still exists after uninstall: {coverage_db}"
    assert elapsed < 900, (
        f"Uninstall deletion took {elapsed:.1f}s — must complete within 900s"
    )
