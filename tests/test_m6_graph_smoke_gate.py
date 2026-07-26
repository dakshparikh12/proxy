"""AC-M6-007 (graph-smoke arm) — the §3.7 graph smoke check gates 'ready'.

The node risk this closes: "graph smoke check present but not actually gating
(no-op)". The coverage gate (indexed + flagged == git ls-files) can be fully
satisfied while the graph itself came back empty / unresolvable. Per §3.7 the
gate is a conjunction: a repo whose classification is complete but whose graph
fails the smoke check is NOT joinable and must reach 'not_ready', never a silent
join over a broken graph.

These tests drive the REAL pipeline; they inject only a broken graph-build result
(never editing the sealed acceptance fixtures) to exercise the failing-smoke path.
"""
from __future__ import annotations

from unittest.mock import patch

import services.code_intel.graph_builder as gb
from services.code_intel.pipeline import run_full_pipeline
from services.code_intel.readiness import ReadinessCollector
from tests.fixtures.repos import small_repo_fixture

_ORIG_BUILD = gb.GraphBuilder.build


def _build_returning_empty_graph(self, clone_path, is_excluded=None, built_at_sha=""):  # type: ignore[no-untyped-def]
    """A build that classifies every file (coverage complete) but yields an empty graph."""
    result = _ORIG_BUILD(self, clone_path, is_excluded=is_excluded, built_at_sha=built_at_sha)
    result.graph.nodes = []  # graph came back empty: the smoke check must fail
    result.graph.index()
    return result


def test_graph_smoke_gate_withholds_ready_on_empty_graph() -> None:
    """A complete-coverage repo whose graph is empty reaches 'not_ready', never 'ready'."""
    fixture = small_repo_fixture()
    collector = ReadinessCollector()

    with patch.object(gb.GraphBuilder, "build", _build_returning_empty_graph):
        pipeline = run_full_pipeline(
            tenant_id="tenant-smoke",
            repo_url=fixture.url,
            readiness_listener=collector,
        )

    assert "ready" not in collector.emitted_states, (
        "Readiness must NOT reach 'ready' when the graph smoke check fails "
        f"(empty graph); emitted={collector.emitted_states}"
    )
    assert "not_ready" in collector.emitted_states, (
        f"Expected 'not_ready' on a failing graph smoke; emitted={collector.emitted_states}"
    )
    record = pipeline.readiness_record
    assert record is not None
    assert record.indexed_at is None, (
        "A withheld (not_ready) record must not carry an indexed_at stamp"
    )


def test_graph_smoke_gate_reaches_ready_on_healthy_graph() -> None:
    """The mirror happy-path: a well-formed repo passes the smoke check and reaches 'ready'."""
    fixture = small_repo_fixture()
    collector = ReadinessCollector()

    pipeline = run_full_pipeline(
        tenant_id="tenant-smoke",
        repo_url=fixture.url,
        readiness_listener=collector,
    )

    assert "ready" in collector.emitted_states, (
        f"A healthy graph must reach 'ready'; emitted={collector.emitted_states}"
    )
    assert len(pipeline.graph.nodes) > 0
    record = pipeline.readiness_record
    assert record is not None and record.indexed_at is not None
