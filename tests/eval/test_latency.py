"""Latency SLO checks for doc01 (AC-LAT-*, evidence_class [performance]).

Marked ``e2e`` (real estate + timing) so the offline tier skips them; the
acceptance command runs them. Numbers are environment-sensitive by nature — a
representative host is required for the SLO to be *meaningful*, but the warm
in-memory graph path is orders of magnitude under the 2s budget on any machine.
"""
from __future__ import annotations

import statistics
import time

import pytest

from services.code_intel.graph_builder import GraphBuilder
from services.code_intel.mcp_server import CodeIntelMCPServer

from tests.eval.test_e2e_estates import _clone_estate, _FLASK_SHA


@pytest.mark.e2e
@pytest.mark.performance
def test_ac_lat_001_direct_answer_p50_under_2s() -> None:
    """AC-LAT-001: 100 warm code_intel tool calls on the direct-answer path —
    p50 <= 2.0s, p95 <= 4.0s."""
    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    graph = GraphBuilder().build(repo / "src").graph  # warm, pre-built
    server = CodeIntelMCPServer(graph=graph, clone_path=repo / "src", lsp=None)
    targets = [n.id for n in graph.nodes if n.kind == "module"] or ["flask.app"]

    samples: list[float] = []
    for i in range(100):
        t0 = time.perf_counter()
        server.get_dependents(targets[i % len(targets)])
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(0.95 * len(samples)) - 1]
    assert p50 <= 2.0, f"p50 {p50 * 1000:.2f}ms exceeds 2.0s"
    assert p95 <= 4.0, f"p95 {p95 * 1000:.2f}ms exceeds 4.0s"


@pytest.mark.e2e
@pytest.mark.performance
def test_ac_lat_002_readiness_within_15_minutes() -> None:
    """AC-LAT-002: a pilot-scale repo reaches Readiness 'ready' within 15 minutes of connect."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)

    class _Listener:
        def __init__(self) -> None:
            self.events: list[str] = []

        def emit(self, state: str) -> None:
            self.events.append(state)

    listener = _Listener()
    t0 = time.perf_counter()
    pipeline = run_full_pipeline(
        tenant_id="lat-002", repo_url=str(repo), readiness_listener=listener
    )
    elapsed = time.perf_counter() - t0

    assert "ready" in listener.events, f"pipeline never reached ready: {listener.events}"
    assert pipeline.readiness_record is not None
    assert elapsed <= 900.0, f"connect->ready took {elapsed:.1f}s, exceeds 900s"


@pytest.mark.e2e
@pytest.mark.performance
def test_ac_lat_003_warm_first_query_resolved_under_2s() -> None:
    """AC-LAT-003: with the host-side resolver warmed (prepared-ahead), the first
    precise query at meeting start returns a warm 'resolved' result within ~2.0s,
    never a cold-spin fallback to grep."""
    from services.code_intel.mcp_server import CodeIntelMCPServer
    from services.code_intel.warm_resolver import PythonSymbolResolver

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    src = repo / "src"
    resolver = PythonSymbolResolver(src)  # warmed on connect/push (prepare-ahead)
    graph = GraphBuilder().build(src).graph
    server = CodeIntelMCPServer(graph=graph, clone_path=src, lsp=resolver)

    t0 = time.perf_counter()
    result = server.find_references("Flask")  # a known class defined in flask.app
    elapsed = time.perf_counter() - t0

    assert result.status == "ok", "warm find_references returned not-found"
    assert result.results and all(i.confidence == "resolved" for i in result.results), (
        "first query not served 'resolved' from the warm resolver (cold-spin fallback?)"
    )
    assert elapsed <= 2.0, f"warm first-query latency {elapsed:.3f}s exceeds 2.0s"
