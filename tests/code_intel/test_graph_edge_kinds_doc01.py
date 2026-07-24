"""Doc 01 · gap D01-GRAPH-EDGE-KINDS-MISSING — the mechanically-built graph must emit
the FULL spec edge-kind vocabulary (§2.2 / §3.4 / spec L147):
``calls | imports | reads | writes | read_write | extends | implements``.

Before the fix the real build (``GraphBuilder().build`` on a real clone) only produced
``calls``/``imports`` and (after prior gaps) ``reads``/``writes`` — but NEVER
``extends``/``implements``. So the class-hierarchy blast radius the spec's core promises —
``get_dependents(BaseClass)`` returning its subclasses over the ``extends`` closure
(R-DOC01-3.5-02: closure over calls/imports/writes/extends/implements) — SILENTLY
under-reported on every real repo: a base-model change looked like it broke nothing.

And ``who_writes`` ran a PARALLEL AST re-scan (``orm.who_writes``) instead of resolving
from the ONE graph the spec specifies (spec L185-190: ``[e for e in graph.edges if
e.target == table_node and e.kind in ('writes','read_write')]``) — two divergent code
paths, the graph one silent.

These tests drive the PRODUCT path: ``run_full_pipeline`` -> the real graph +
``CodeIntelMCPServer.get_dependents`` / ``.who_writes`` on a real on-disk repo. No
injected doubles, no ``from_spec`` fixture graph — the REAL mechanical build.
"""
from __future__ import annotations

import pathlib
import subprocess


def _git_init(root: pathlib.Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "init"],
        check=True,
    )


# --------------------------------------------------------------------------- #
# extends / implements — Python class hierarchy                               #
# --------------------------------------------------------------------------- #
_PY_HIER = """\
class BaseHandler:
    def handle(self):
        raise NotImplementedError


class Protocol:
    def send(self):
        ...


class HttpHandler(BaseHandler, Protocol):
    def handle(self):
        return "ok"


class ApiHandler(HttpHandler):
    pass
"""


def _build_hier_repo(root: pathlib.Path) -> pathlib.Path:
    pkg = root / "svc"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "handlers.py").write_text(_PY_HIER)
    _git_init(root)
    return root


def test_build_emits_extends_edges_from_class_bases(tmp_path: pathlib.Path) -> None:
    """The real build must EMIT ``extends`` edges from each subclass to its base class
    node — ``HttpHandler --extends--> BaseHandler``, ``ApiHandler --extends--> HttpHandler``
    — so a class hierarchy is a first-class part of the ONE graph, not absent."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_hier_repo(tmp_path / "hier")
    graph = run_full_pipeline(tenant_id="t-ext", repo_url=str(repo)).server.graph

    extends = {(e.source, e.target) for e in graph.edges if e.kind == "extends"}
    rel = "svc/handlers.py"
    assert (f"{rel}::HttpHandler", f"{rel}::BaseHandler") in extends, extends
    assert (f"{rel}::ApiHandler", f"{rel}::HttpHandler") in extends, extends


def test_build_emits_implements_edges_for_protocol_bases(tmp_path: pathlib.Path) -> None:
    """A class with MULTIPLE bases records the secondary/mixin bases as ``implements``
    edges (interface-style), so both the primary hierarchy and the mixed-in contracts
    are on the graph. ``HttpHandler`` extends ``BaseHandler`` (primary) and implements
    ``Protocol`` (secondary base)."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_hier_repo(tmp_path / "impl")
    graph = run_full_pipeline(tenant_id="t-impl", repo_url=str(repo)).server.graph

    rel = "svc/handlers.py"
    impls = {(e.source, e.target) for e in graph.edges if e.kind == "implements"}
    assert (f"{rel}::HttpHandler", f"{rel}::Protocol") in impls, impls


def test_get_dependents_blast_radius_over_class_hierarchy(tmp_path: pathlib.Path) -> None:
    """R-DOC01-3.5-02 on REAL data: ``get_dependents(BaseHandler)`` must return the
    transitive subclass set over the ``extends``/``implements`` closure — the class-
    hierarchy blast radius. ``ApiHandler --extends--> HttpHandler --extends--> BaseHandler``
    means changing ``BaseHandler`` reaches BOTH ``HttpHandler`` and ``ApiHandler``.

    Before the fix (no extends edges) this returned an empty/under-reported set — a
    silent 'changing the base breaks nothing' answer, the exact blast-radius miss the
    gap names.
    """
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_hier_repo(tmp_path / "blast")
    server = run_full_pipeline(tenant_id="t-blast", repo_url=str(repo)).server

    res = server.get_dependents("BaseHandler", limit=50)
    dep_ids = {r.id for r in res.results}
    rel = "svc/handlers.py"
    assert f"{rel}::HttpHandler" in dep_ids, dep_ids
    # transitive over the extends closure — ApiHandler extends HttpHandler extends Base.
    assert f"{rel}::ApiHandler" in dep_ids, (
        f"transitive class-hierarchy blast radius under-reported: {dep_ids}"
    )
    assert res.status == "ok"


# --------------------------------------------------------------------------- #
# who_writes — resolved from the ONE graph, not a parallel AST re-scan        #
# --------------------------------------------------------------------------- #
_MODELS = """\
from django.db import models


class Order(models.Model):
    total = models.IntegerField()
"""

# A function that BOTH reads and writes the Order table => ONE read_write edge (spec
# §12.6 kind-per-verb: a co-accessor that reads AND writes is `read_write`, not two
# edges), and it must count as a writer for who_writes (writes ∪ read_write).
_SVC = """\
from shared.models import Order


def reconcile_order(pk):
    o = Order.objects.get(pk=pk)      # read
    o.total = o.total + 1
    o.save()                           # write  => reconcile_order is read_write
    return o


def just_read(pk):
    return Order.objects.get(pk=pk)    # pure read
"""


def _build_rw_repo(root: pathlib.Path) -> pathlib.Path:
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("")
    (shared / "models.py").write_text(_MODELS)
    app = root / "ops"
    app.mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("")
    (app / "svc.py").write_text(_SVC)
    _git_init(root)
    return root


def test_build_emits_read_write_edge_for_read_and_write(tmp_path: pathlib.Path) -> None:
    """A function that both READS and WRITES the same table gets ONE ``read_write`` edge
    into ``table::Order`` (spec kind-per-verb), never a duplicate reads+writes pair."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_rw_repo(tmp_path / "rw")
    graph = run_full_pipeline(tenant_id="t-rw", repo_url=str(repo)).server.graph

    src = "ops/svc.py::reconcile_order"
    kinds_for_src = {
        e.kind for e in graph.edges
        if e.source == src and e.target == "table::Order"
    }
    assert kinds_for_src == {"read_write"}, (
        f"reconcile_order reads AND writes Order => exactly one read_write edge, got {kinds_for_src}"
    )


def test_who_writes_resolves_from_the_graph(tmp_path: pathlib.Path) -> None:
    """``who_writes('order')`` must resolve from the ONE graph (writes ∪ read_write edges
    into the table node) — the same graph ``shares_table`` reads — not a divergent
    ``orm.who_writes`` re-scan. A read_write toucher (``reconcile_order``) counts as a
    writer; a pure reader (``just_read``) does not."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_rw_repo(tmp_path / "ww")
    server = run_full_pipeline(tenant_id="t-ww", repo_url=str(repo)).server

    res = server.who_writes("order")
    writer_ids = {w.id for w in res.writers}
    assert "ops/svc.py::reconcile_order" in writer_ids, writer_ids
    assert "ops/svc.py::just_read" not in writer_ids, (
        f"pure reader misclassified as a writer: {writer_ids}"
    )
    assert res.status == "ok"
    for w in res.writers:
        assert w.confidence == "resolved", (w.id, w.confidence)
