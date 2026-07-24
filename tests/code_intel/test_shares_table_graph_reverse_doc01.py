"""Doc 01 · gap D01-SHARES-TABLE-NOT-GRAPH — ``shares_table`` must be a GRAPH read over
the reverse reads/writes edges into the ``table::<Name>`` node, grouped to the owning
module, and it must return ``touchers`` (file:line leads) + a ``shared`` boolean.

Real-data failure reproduced on a real 2-app Django repo: BOTH ``billing/svc.py`` and
``sales/svc.py`` write the ``Invoice`` table. ``billing`` writes via the classic
``Invoice.objects.create(...)`` form; ``sales`` writes via an INSTANCE PARAMETER
``def post_invoice(i): i.save()`` — which the pre-fix substring/regex
(``'Invoice.objects' in text`` / ``Invoice(...)``) never matched, so
``shares_table('invoice')`` silently returned only ``['billing']`` and MISSED ``sales``.
That drops exactly the hidden cross-module coupling the value-prop promises to catch.

Drives the PRODUCT path: ``run_full_pipeline`` -> the real
``CodeIntelMCPServer.shares_table`` on a real on-disk Django repo. No injected doubles.
"""
from __future__ import annotations

import pathlib
import subprocess

_MODELS = """\
from django.db import models


class Invoice(models.Model):
    customer = models.CharField(max_length=200)
    total = models.IntegerField()
    status = models.CharField(max_length=32, default="draft")
"""

# billing writes Invoice the classic way — Invoice.objects / Invoice(...)
_BILLING_SVC = """\
from shared.models import Invoice


def create_invoice(customer, total):
    return Invoice.objects.create(customer=customer, total=total)
"""

# sales writes Invoice via an INSTANCE PARAMETER — i.save() — the form the old
# ``\'Invoice.objects\' in text`` / ``Invoice(...)`` regex could never see.
_SALES_SVC = """\
from shared.models import Invoice


def post_invoice(i: Invoice):
    i.status = "posted"
    i.save()
    return i
"""


def _build_repo(root: pathlib.Path) -> pathlib.Path:
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("")
    (shared / "models.py").write_text(_MODELS)
    for app, src in (("billing", _BILLING_SVC), ("sales", _SALES_SVC)):
        d = root / app
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")
        (d / "svc.py").write_text(src)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "init"],
        check=True,
    )
    return root


def test_shares_table_catches_instance_write_co_accessor(tmp_path: pathlib.Path) -> None:
    """shares_table('invoice') must name BOTH billing and sales — sales writes via the
    instance-param ``i.save()`` the pre-fix regex missed — through the real tool minted
    by run_full_pipeline, and expose touchers[file:line] + shared=True."""
    from services.code_intel import orm
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "invoice-app")
    assert orm.is_tier1(repo) is True, "real Django stack must be tier-1"

    pipeline = run_full_pipeline(tenant_id="t-shares", repo_url=str(repo))
    server = pipeline.server
    assert server is not None, "run_full_pipeline must attach the real MCP server"

    result = server.shares_table("invoice")
    modules = {m.id for m in result.modules}

    assert "billing" in modules, modules
    assert "sales" in modules, (
        f"shares_table dropped the instance-write co-accessor (sales): {modules}"
    )
    assert result.status == "ok"

    # SHAPE: the spec return contract requires touchers (file:line leads) and a
    # ``shared`` boolean the §3.8 example / direct-answer citation path consume.
    assert result.shared is True, "two writing modules => shared coupling"
    toucher_files = {t.file for t in result.touchers}
    assert "billing/svc.py" in toucher_files, toucher_files
    assert "sales/svc.py" in toucher_files, toucher_files
    for t in result.touchers:
        assert t.line >= 1, t
        assert t.confidence == "resolved", f"{t.file} tagged {t.confidence!r}"


def test_shares_table_single_module_not_shared(tmp_path: pathlib.Path) -> None:
    """A table touched by only ONE module is not 'shared' — shared=len(modules)>1."""
    from services.code_intel.pipeline import run_full_pipeline

    root = tmp_path / "solo-app"
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("")
    (shared / "models.py").write_text(_MODELS)
    d = root / "billing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("")
    (d / "svc.py").write_text(_BILLING_SVC)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "init"],
        check=True,
    )

    server = run_full_pipeline(tenant_id="t-solo", repo_url=str(root)).server
    result = server.shares_table("invoice")
    assert {m.id for m in result.modules} == {"billing"}
    assert result.shared is False, "single writing module => not shared"
    assert {t.file for t in result.touchers} == {"billing/svc.py"}


def test_graph_carries_writes_edges_into_table_node(tmp_path: pathlib.Path) -> None:
    """The build must EMIT real ``writes`` edges into the ``table::Invoice`` node — the
    D01-GRAPH-EDGE-KINDS prerequisite. Both billing (Invoice.objects) and sales (i.save())
    must appear as writes edges, proving the graph (not a regex) is the source."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "invoice-graph")
    pipeline = run_full_pipeline(tenant_id="t-graph", repo_url=str(repo))
    graph = pipeline.server.graph

    write_edges = [
        e for e in graph.edges
        if e.kind == "writes" and e.target == "table::Invoice"
    ]
    sources = {e.source for e in write_edges}
    assert "billing/svc.py::create_invoice" in sources, sources
    assert "sales/svc.py::post_invoice" in sources, (
        f"instance-write co-accessor missing from graph writes edges: {sources}"
    )


def test_direct_answer_cites_shares_table_toucher(tmp_path: pathlib.Path) -> None:
    """A 'who else shares this table' wake turn must resolve to a grounded file:line
    citation through the real direct-answer path — proving touchers feed _first_hit."""
    from services.code_intel.direct_answer import answer_direct
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "invoice-da")
    server = run_full_pipeline(tenant_id="t-da", repo_url=str(repo)).server

    ans = answer_direct(
        ask="which modules also share the invoice table?",
        tenant="t-da",
        sha="",
        e2b=None,
        workroom=None,
        code_intel=server,
    )
    assert ans.tool == "shares_table", ans.tool
    assert ans.citation is not None, f"shares_table ask abstained: {ans.text}"
    assert ":" in ans.citation
