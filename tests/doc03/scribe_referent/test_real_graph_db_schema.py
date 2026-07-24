"""AC-REFM-04 integration tier — the referent matcher binds against a graph.db
produced by the REAL Doc-01 GraphStore (schema id/kind/file_path/line/exported/
built_at_sha), not a hand-built ``graph_nodes(node_id, area, file, symbol)`` double.

This is the missing integration tier the criterion mandates
(``mock_boundary: real db only; no in-memory substitute for the integration tier``).
The synthetic-schema conftest hid a schema mismatch: the matcher SELECTed
``node_id, area, file, symbol`` while the real per-repo graph.db carries
``id, kind, file_path, line, exported, built_at_sha`` — so every lookup silently
returned ``None``. These tests drive the REAL seam:

* ``code_intel.graph_store.GraphStore.write_graph`` writes the canonical schema,
* ``scribe.referent.lookup_referent`` reads it and binds a term to a REAL node id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The real Doc-01 graph store + node model (the canonical writer of graph.db).
_CI_SRC = Path(__file__).resolve().parents[3] / "services" / "code_intel" / "src"
if str(_CI_SRC) not in sys.path:
    sys.path.insert(0, str(_CI_SRC))

from code_intel.graph import Edge, Graph, Node  # noqa: E402
from code_intel.graph_store import GraphStore  # noqa: E402

from scribe.referent import ReferentCorpus, lookup_referent  # noqa: E402


# Real Doc-01 canonical id forms (verified against the flask graph.db, 1380 nodes):
#   symbol:  "payments/charge.py::ChargeProcessor.charge"  (leaf after '::')
#   function:"payments/checkout.py::checkout"
#   module:  "docs.conf" (dotted; file_path is the real file)
#   table:   "table::refunds"
_REAL_NODES = [
    Node(id="payments/checkout.py::checkout", path="payments/checkout.py", line=10,
         kind="function", exported=1, built_at_sha="deadbeef"),
    Node(id="payments/charge.py::ChargeProcessor.charge", path="payments/charge.py",
         line=22, kind="method", exported=1, built_at_sha="deadbeef"),
    Node(id="payments/refund.py::issue_refund", path="payments/refund.py", line=5,
         kind="function", exported=1, built_at_sha="deadbeef"),
    Node(id="docs.conf", path="docs/conf.py", line=1, kind="module",
         exported=0, built_at_sha="deadbeef"),
    Node(id="table::refunds", path="payments/models.py", line=3, kind="table",
         exported=1, built_at_sha="deadbeef"),
]


@pytest.fixture
def real_graph_db(tmp_path: Path) -> str:
    """A graph.db written by the REAL GraphStore (canonical id/kind/file_path schema)."""
    db_path = tmp_path / "graph.db"
    graph = Graph(nodes=list(_REAL_NODES), edges=[Edge(
        source="payments/checkout.py::checkout",
        target="payments/charge.py::ChargeProcessor.charge",
        kind="calls", file_path="payments/checkout.py", line=12)])
    GraphStore(db_path).write_graph(graph, drop_first=True)
    return str(db_path)


def test_symbol_binds_against_real_schema(real_graph_db: str) -> None:
    """A code-sounding term binds to a REAL node id in the real-schema graph.db."""
    corpus = ReferentCorpus(db_path=real_graph_db)
    result = lookup_referent("checkout", corpus)
    assert result == "payments/checkout.py::checkout"


def test_method_leaf_binds_against_real_schema(real_graph_db: str) -> None:
    corpus = ReferentCorpus(db_path=real_graph_db)
    # 'charge' is the leaf of 'payments/charge.py::ChargeProcessor.charge'.
    result = lookup_referent("charge", corpus)
    assert result == "payments/charge.py::ChargeProcessor.charge"


def test_module_binds_against_real_schema(real_graph_db: str) -> None:
    corpus = ReferentCorpus(db_path=real_graph_db)
    # 'conf' is the leaf of the dotted module id 'docs.conf'.
    result = lookup_referent("conf", corpus)
    assert result == "docs.conf"


def test_table_binds_against_real_schema(real_graph_db: str) -> None:
    corpus = ReferentCorpus(db_path=real_graph_db)
    # 'refunds' is the leaf of 'table::refunds'.
    result = lookup_referent("refunds", corpus)
    assert result == "table::refunds"


def test_returned_id_is_real_not_fabricated(real_graph_db: str) -> None:
    corpus = ReferentCorpus(db_path=real_graph_db)
    known = {n.id for n in _REAL_NODES}
    for term in ["checkout", "charge", "issue_refund", "conf", "refunds"]:
        result = lookup_referent(term, corpus)
        assert result is None or result in known


def test_unknown_term_stays_unbound(real_graph_db: str) -> None:
    corpus = ReferentCorpus(db_path=real_graph_db)
    assert lookup_referent("no_such_symbol_xyz", corpus) is None
