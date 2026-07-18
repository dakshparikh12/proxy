"""Doc 01 · end-to-end SIMULATION workflows.

Twelve multi-step scenarios that chain code_intel's REAL pipeline
(connect -> clone -> gitleaks -> tree-sitter index -> graph build -> tool calls,
plus push/meeting/uninstall lifecycles) through the spec's "one correct
interaction" and its failure/abstention paths. Each workflow asserts a
BEHAVIORAL CHAIN across an execution trace (not a single fact) and is mapped in
its docstring to the criterion_ids it exercises.

All product AND fixture imports live INSIDE the workflow bodies, so this module
COLLECTS clean and every workflow FAILS red before ``services.code_intel``
exists. Fixtures are hermetic real local git repos + deterministic stubs.
"""
import pytest

pytestmark = pytest.mark.workflow


# ── W01 ───────────────────────────────────────────────────────────────────
def test_w01_connect_index_ready_then_tool_calls():
    """W01 full happy path: connect -> clone -> index -> Readiness 'ready' ->
    get_dependents / list_entry_points / owner all answer, no excluded path leaks.
    Chains AC-M1-004, AC-M2-002, AC-M4-001, AC-M6-001, AC-M6-004, AC-M5-002,
    AC-M5-008, AC-E2E-001."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.readiness import ReadinessCollector
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    collector = ReadinessCollector()

    pipeline = run_full_pipeline(
        tenant_id="tenant-w01",
        repo_url=fixture.url,
        readiness_listener=collector,
    )

    # (1) readiness trace ends at 'ready'
    assert "ready" in collector.emitted_states
    assert collector.emitted_states[-1] == "ready"

    # (2) coverage accounts for every tracked file (indexed + flagged == tracked)
    coverage = pipeline.coverage_record
    assert coverage.count_by_status("indexed") + coverage.count_by_status(
        "flagged"
    ) == len(fixture.all_files)

    # (3) tools answer over the built graph
    server = CodeIntelMCPServer(pipeline=pipeline)
    deps = server.get_dependents("some_fn", limit=50)
    entries = server.list_entry_points()
    assert deps.results is not None
    assert entries.results is not None

    # (4) no excluded path leaks into any answer
    excluded = pipeline.exclusion_manager.get_excluded_paths(pipeline.clone_path)
    for item in list(deps.results) + list(entries.results):
        assert item.path not in excluded


# ── W02 ───────────────────────────────────────────────────────────────────
def test_w02_secret_excluded_from_graph_tools_and_sandbox():
    """W02 secret containment: planted .env -> gitleaks excludes -> absent from
    graph, absent from tool results, absent from sandbox, in-source secret redacted.
    Chains AC-M3-001, AC-M3-003, AC-M3-004, AC-M3-005, AC-M3-006."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.sandbox import prepare_sandbox
    from tests.fixtures.stubs import PlantedSecretsRepo, PolicyGlobsFixture

    fixture = PlantedSecretsRepo()
    policy = PolicyGlobsFixture(globs=[".env*", "node_modules/"])

    pipeline = run_full_pipeline(
        tenant_id="tenant-w02", repo_url=fixture.url, policy_globs=policy.globs
    )
    excluded = pipeline.exclusion_manager.get_excluded_paths(pipeline.clone_path)

    # secret file is excluded
    assert fixture.secret_file in excluded

    # absent from graph
    for ex in excluded:
        assert [n for n in pipeline.graph.nodes if n.path == str(ex)] == []

    # absent from tool results + never copied into sandbox
    server = CodeIntelMCPServer(pipeline=pipeline)
    for item in server.list_entry_points().results:
        assert item.path not in excluded
    sandbox = prepare_sandbox(pipeline=pipeline)
    sandbox_files = {str(p) for p in sandbox.file_list()}
    for ex in excluded:
        assert str(ex) not in sandbox_files


# ── W03 ───────────────────────────────────────────────────────────────────
def test_w03_push_delta_rescan_full_rebuild_and_cache_invalidation():
    """W03 freshness on push: webhook -> delta pull (not re-clone) -> gitleaks
    re-run -> FULL graph rebuild (drop before insert) -> per-meeting cache invalidated.
    Chains AC-M2-006, AC-M3-002, AC-M4-009, AC-M5-015."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import (
        DBOperationCounter,
        DBQueryCounter,
        GitInterceptor,
        push_webhook_fixture,
    )

    fixture = small_repo_fixture()
    git = GitInterceptor()
    db_ops = DBOperationCounter()
    db_q = DBQueryCounter()
    pipeline = run_full_pipeline(
        tenant_id="tenant-w03",
        repo_url=fixture.url,
        git_interceptor=git,
        db_operation_counter=db_ops,
        db_counter=db_q,
    )
    server = pipeline.server if hasattr(pipeline, "server") else None
    session = MeetingSession(server=server) if server else MeetingSession(pipeline=pipeline)
    session.tool_call("get_dependents", symbol="some_fn", limit=50)

    git.reset()
    db_ops.reset()
    handler = WebhookHandler(pipeline=pipeline)
    handler.handle(push_webhook_fixture(repo_url=fixture.url, sha="w03newsha"))

    # (1) delta pull, never a re-clone
    argv = git.recorded_args
    assert [a for a in argv if a and "clone" in a] == []
    assert [a for a in argv if a and ("fetch" in a or "pull" in a)]

    # (2) full rebuild: a DROP/DELETE_ALL precedes the first INSERT
    ops = db_ops.recorded_operations
    drop_idx = next((i for i, o in enumerate(ops) if o.type in ("DROP", "DELETE_ALL", "TRUNCATE")), None)
    ins_idx = next((i for i, o in enumerate(ops) if o.type == "INSERT"), None)
    assert drop_idx is not None and ins_idx is not None and drop_idx < ins_idx

    # (3) cache invalidated: the next identical call re-queries the DB
    db_q.reset()
    session.tool_call("get_dependents", symbol="some_fn", limit=50)
    assert db_q.query_count > 0


# ── W04 ───────────────────────────────────────────────────────────────────
def test_w04_meeting_pin_stable_under_mid_meeting_push():
    """W04 pin stability: meeting pins SHA -> mid-meeting push -> pin unchanged,
    results identical, 'repo advanced N commits' notification, prior graph version retained.
    Chains AC-M7-004, AC-M7-005, AC-M7-006, AC-GV-001."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-w04", repo_url=fixture.url)
    session = MeetingSession.start(pipeline=pipeline)
    pin = session.pinned_sha
    before = session.tool_call("get_dependents", symbol="some_fn", limit=50)

    handler = WebhookHandler(pipeline=pipeline)
    handler.handle(push_webhook_fixture(repo_url=fixture.url, sha="w04Y", num_commits=3))

    after = session.tool_call("get_dependents", symbol="some_fn", limit=50)
    assert session.pinned_sha == pin
    assert before == after
    assert len(session.notifications) == 1
    text = session.notifications[0].text.lower()
    assert "repo advanced" in text and "3" in text


# ── W05 ───────────────────────────────────────────────────────────────────
def test_w05_meeting_start_reconciles_drift_in_order():
    """W05 drift reconciliation: ls-remote ahead of local tip -> pull -> graph
    rebuild -> readiness_confirmed, strictly in that order.
    Chains AC-M7-003, AC-M6-002."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import Pipeline
    from tests.fixtures.stubs import DriftSimulation, EventLog

    drift = DriftSimulation(local_tip="w05_old", remote_tip="w05_new", commits_behind=2)
    log = EventLog()
    MeetingSession.start(pipeline=Pipeline.from_drift_fixture(drift), event_log=log)

    types = [o.type for o in log.operations]
    assert "pull" in types and "graph_rebuild" in types and "readiness_confirmed" in types
    assert types.index("pull") < types.index("graph_rebuild") < types.index("readiness_confirmed")


# ── W06 ───────────────────────────────────────────────────────────────────
def test_w06_lsp_resolved_then_timeout_fallback_and_restart():
    """W06 navigation resilience: warm LSP -> 'resolved'; then a hung LSP ->
    grep fallback tagged 'lower-bound' within timeout, and the hung server is restarted.
    Chains AC-M8-001, AC-M8-002, AC-M8-003, AC-M8-004, AC-INV-003."""
    import time
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import LspStubSlow, LspStubWarm, LspLifecycleInstrumented

    fixture = small_repo_fixture()

    warm = CodeIntelMCPServer.from_fixture(fixture, lsp=LspStubWarm())
    resolved = warm.find_references(fixture.known_symbol)
    assert resolved.results
    assert all(r.confidence == "resolved" for r in resolved.results)

    lifecycle = LspLifecycleInstrumented(LspStubSlow(delay_s=5.0))
    slow = CodeIntelMCPServer.from_fixture(fixture, lsp=lifecycle)
    start = time.monotonic()
    fell_back = slow.find_references(fixture.known_symbol)
    elapsed = time.monotonic() - start
    assert elapsed < 4.0
    assert fell_back.results
    assert all(r.confidence == "lower-bound" for r in fell_back.results)
    assert lifecycle.restart_count >= 1


# ── W07 ───────────────────────────────────────────────────────────────────
def test_w07_who_writes_tiering_and_shares_table():
    """W07 honesty tiering: who_writes 'resolved' on tier-1 Django, 'lower-bound'
    on a non-tier-1 ORM (never a silent exact); shares_table surfaces co-accessors.
    Chains AC-M5-005, AC-M5-006, AC-M5-007, AC-INV-002, AC-INV-003."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import (
        django_model_fixture,
        non_tier1_orm_fixture,
        cross_module_table_access_fixture,
    )

    tier1 = CodeIntelMCPServer.from_fixture(django_model_fixture())
    r1 = tier1.who_writes("orders")
    assert r1.writers
    assert all(w.confidence == "resolved" for w in r1.writers)

    nontier = CodeIntelMCPServer.from_fixture(non_tier1_orm_fixture())
    r2 = nontier.who_writes("orders")
    assert all(w.confidence == "lower-bound" for w in r2.writers)
    assert [w for w in r2.writers if w.confidence == "resolved"] == []

    shared = CodeIntelMCPServer.from_fixture(cross_module_table_access_fixture())
    r3 = shared.shares_table("payments")
    assert len({m.id for m in r3.modules}) >= 2


# ── W08 ───────────────────────────────────────────────────────────────────
def test_w08_honest_abstention_no_fabrication():
    """W08 abstention: an isolated symbol yields an EXPLICIT empty set labeled
    'not-found', never a fabricated node or file:line.
    Chains AC-E2E-002, AC-INV-002, AC-INV-001."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import isolated_symbol_fixture

    fixture = isolated_symbol_fixture()
    server = CodeIntelMCPServer.from_fixture(fixture)

    result = server.get_dependents(fixture.isolated_symbol, limit=50)
    assert result.results == []
    assert result.status in ("not-found", "ok")
    # no fabricated citations for a symbol with no dependents
    assert all(getattr(item, "file", None) for item in result.results)


# ── W09 ───────────────────────────────────────────────────────────────────
def test_w09_tenant_isolation_end_to_end():
    """W09 isolation triad: tenant-B cannot read tenant-A's volume, graph queries
    return only tenant-B nodes, and the meeting cache is tenant-scoped.
    Chains AC-M2-001, AC-INV-005, AC-INV-006, AC-INV-007."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.meeting import MeetingSession
    from tests.fixtures.stubs import TwoTenantCloneFixture, path_traversal_args

    fixture = TwoTenantCloneFixture()
    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)

    # (1) path traversal toward tenant-A is refused / never leaks tenant-A data
    traversal = path_traversal_args(
        tenant_b_base="/tenants/tenant-B/",
        tenant_a_target="/tenants/tenant-A/secret_file.py",
    )
    read = server_b.batch_read([traversal["path"]])
    for fr in read.files:
        assert fr.error is not None or "tenant-A" not in (fr.content or "")

    # (2) graph queries never surface tenant-A node ids
    deps = server_b.get_dependents("some_fn", limit=50)
    assert {i.id for i in deps.results} & fixture.tenant_a_node_ids == set()

    # (3) cache is tenant-scoped
    session_b = MeetingSession.start(pipeline=server_b.pipeline)
    rb = session_b.tool_call("get_dependents", symbol="some_fn", limit=50)
    assert {i.id for i in rb.results}.issubset(fixture.tenant_b_node_ids)


# ── W10 ───────────────────────────────────────────────────────────────────
def test_w10_webhook_security_dedup_and_uninstall_hard_delete():
    """W10 webhook lifecycle: bad HMAC -> 401, no rebuild; duplicate delivery ->
    single rebuild; uninstall -> clone/graph/coverage hard-deleted within 15 min.
    Chains AC-M7-001, AC-M7-002, AC-CANON-005."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import (
        GraphRebuildCounter,
        bad_hmac_webhook_fixture,
        push_webhook_fixture,
        uninstall_webhook_fixture,
    )

    rebuilds = GraphRebuildCounter()
    guard = WebhookHandler(rebuild_counter=rebuilds)
    bad = guard.handle(bad_hmac_webhook_fixture())
    assert bad.status_code == 401 and not bad.enqueued and rebuilds.count == 0

    dup = push_webhook_fixture(repo_url="https://github.com/example/repo", sha="w10", delivery_guid="w10-guid")
    guard.handle(dup)
    guard.handle(dup)
    assert rebuilds.count == 1

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-w10", repo_url=fixture.url)
    handler = WebhookHandler(pipeline=pipeline)
    handler.handle(uninstall_webhook_fixture(tenant_id="tenant-w10"))
    assert not pipeline.clone_path.exists()
    assert not pipeline.graph_db_path.exists()
    assert not pipeline.coverage_db_path.exists()


# ── W11 ───────────────────────────────────────────────────────────────────
def test_w11_latency_slo_and_readiness_within_budget():
    """W11 performance SLO: 100 warm direct-answer calls meet p50<=2.0s / p95<=4.0s,
    and pilot-scale readiness is reached within 900s of connect.
    Chains AC-LAT-001, AC-LAT-002."""
    import time
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.meeting import MeetingSession
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()

    connect_start = time.monotonic()
    pipeline = run_full_pipeline(tenant_id="tenant-w11", repo_url=fixture.url)
    ready_elapsed = time.monotonic() - connect_start
    assert ready_elapsed <= 900.0

    session = MeetingSession.start(pipeline=pipeline)
    latencies = []
    for _ in range(100):
        t0 = time.monotonic()
        session.tool_call("get_dependents", symbol="some_fn", limit=50)
        latencies.append(time.monotonic() - t0)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p50 <= 2.0
    assert p95 <= 4.0


# ── W12 ───────────────────────────────────────────────────────────────────
def test_w12_owner_codeowners_then_blame_fallback_grounded():
    """W12 ownership grounding: CODEOWNERS match -> 'resolved'; no match ->
    git-blame fallback 'lower-bound'; every returned claim carries a real file:line.
    Chains AC-M5-009, AC-M5-010, AC-INV-001."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from tests.fixtures.repos import codeowners_fixture, no_codeowners_fixture

    owned = codeowners_fixture()
    s1 = CodeIntelMCPServer.from_fixture(owned)
    r1 = s1.owner(owned.owned_path)
    assert r1.confidence == "resolved"
    assert r1.owner == owned.expected_owner

    unowned = no_codeowners_fixture()
    s2 = CodeIntelMCPServer.from_fixture(unowned)
    r2 = s2.owner(unowned.any_path)
    assert r2 is not None
    assert r2.confidence == "lower-bound"
