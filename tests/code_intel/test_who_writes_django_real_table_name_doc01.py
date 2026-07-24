"""Doc 01 · gap D01-DJANGO-TABLE-NAME — who_writes / shares_table must resolve the
REAL Django DB table name, not only the (lowercased) model class name.

Django's default table for a model with no explicit ``Meta.db_table`` is
``<app_label>_<model_lower>`` — for ``shop/models.py::Order`` the real table is
``shop_order`` (app_label = the models.py package dir). A PM/DBA in a schema-change
discussion asks about the REAL table name ('what writes shop_order?'), so
``who_writes('shop_order')`` must resolve — pre-fix it returned not-found because the
map only keyed on the bare model name ('order'). A model that DOES set
``Meta.db_table`` already resolved (that explicit name is authoritative); this gap is
the *default* table name.

Drives the PRODUCT path: ``run_full_pipeline`` -> the real
``CodeIntelMCPServer.who_writes`` / ``.shares_table`` on a real on-disk Django repo,
plus the real graph table node stamped by the pipeline's GraphBuilder. No doubles.
"""
from __future__ import annotations

import pathlib
import subprocess

_MODELS = '''\
from django.db import models


class Order(models.Model):
    customer = models.CharField(max_length=200)
    total = models.IntegerField()


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    amount = models.IntegerField()


class Invoice(models.Model):
    total = models.IntegerField()

    class Meta:
        db_table = "billing_invoice"
'''

_SERVICES = '''\
from .models import Order, Refund, Invoice


def create_order(customer, total):
    order = Order(customer=customer, total=total)
    order.save()
    return order


def issue_refund(order, amount):
    return Refund.objects.create(order=order, amount=amount)


def bill(total):
    return Invoice.objects.create(total=total)
'''


def _build_repo(root: pathlib.Path) -> pathlib.Path:
    shop = root / "shop"
    shop.mkdir(parents=True, exist_ok=True)
    (shop / "__init__.py").write_text("")
    (shop / "models.py").write_text(_MODELS)
    (shop / "services.py").write_text(_SERVICES)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "init"],
        check=True,
    )
    return root


def test_django_who_writes_resolves_real_default_table_name(tmp_path: pathlib.Path) -> None:
    """who_writes('shop_order') / who_writes('shop_refund') — the REAL default Django
    table names (app_label 'shop' + model) — must resolve through the real tool minted
    by run_full_pipeline, tagged resolved, and agree with the bare-model-name query."""
    from services.code_intel import orm
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "shop-app")
    assert orm.is_tier1(repo) is True, "real Django stack must be tier-1"

    pipeline = run_full_pipeline(tenant_id="t-django-realtable", repo_url=str(repo))
    server = pipeline.server
    assert server is not None, "run_full_pipeline must attach the real MCP server"

    real_order = server.who_writes("shop_order")
    real_order_ids = {w.id for w in real_order.writers}
    assert real_order.status == "ok", real_order.status
    assert "shop/services.py::create_order" in real_order_ids, real_order_ids

    real_refund = server.who_writes("shop_refund")
    real_refund_ids = {w.id for w in real_refund.writers}
    assert real_refund.status == "ok", real_refund.status
    assert "shop/services.py::issue_refund" in real_refund_ids, real_refund_ids

    assert real_order_ids == {w.id for w in server.who_writes("order").writers}
    assert real_refund_ids == {w.id for w in server.who_writes("refund").writers}

    assert {w.id for w in server.who_writes("billing_invoice").writers} == {
        "shop/services.py::bill"
    }

    for w in [*real_order.writers, *real_refund.writers]:
        assert w.confidence == "resolved", f"{w.id} tagged {w.confidence!r}"


def test_django_shares_table_resolves_real_default_table_name(tmp_path: pathlib.Path) -> None:
    """shares_table('shop_order') — the real default table name — resolves the same
    co-accessing modules as shares_table('order'), through the real minted tool."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "shop-app2")
    pipeline = run_full_pipeline(tenant_id="t-django-shares", repo_url=str(repo))
    server = pipeline.server
    assert server is not None

    by_real = {m.id for m in server.shares_table("shop_order").modules}
    by_model = {m.id for m in server.shares_table("order").modules}
    assert by_real, "real default table name must resolve co-accessors"
    assert by_real == by_model, (by_real, by_model)


def test_django_graph_table_node_carries_real_table_name(tmp_path: pathlib.Path) -> None:
    """The dependency graph the pipeline builds stamps a node keyed to the REAL default
    table name (``table::shop_order``) so a schema-change lookup by the real table name
    lands on the graph node — alongside the canonical ``table::Order`` (AC-M4-008)."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "shop-app3")
    pipeline = run_full_pipeline(tenant_id="t-django-node", repo_url=str(repo))
    graph = pipeline.graph
    node_ids = {n.id for n in graph.nodes}

    assert "table::Order" in node_ids, sorted(i for i in node_ids if i.startswith("table::"))
    assert "table::shop_order" in node_ids, sorted(
        i for i in node_ids if i.startswith("table::")
    )
    assert "table::billing_invoice" in node_ids, sorted(
        i for i in node_ids if i.startswith("table::")
    )
