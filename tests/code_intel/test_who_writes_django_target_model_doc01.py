"""Doc 01 · gap D01-WHO-WRITES-OVERATTRIBUTES — who_writes must resolve the ACTUAL
target model of each Django write, not blast every write in a file onto every table
whose model is imported there (§11.12 gate-(b) accuracy contract, Law 2).

Real-data failure reproduced on a real Django app: two models ``Order`` and ``Refund``
imported into one ``shop/services.py``. ``create_order`` only ever does
``Order.objects.create(...)`` / ``order.save()`` — it never touches ``Refund`` —
and ``issue_refund`` only ever writes ``Refund``. The pre-fix Django branch returned
an IDENTICAL writer set for ``who_writes('order')`` and ``who_writes('refund')``
(both ``[create_order, issue_refund]``), each tagged ``resolved`` — a confident wrong
exact blast-radius on a tier-1 stack.

Drives the PRODUCT path: ``run_full_pipeline`` -> the real
``CodeIntelMCPServer.who_writes`` on a real on-disk Django repo. No injected doubles.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

_SHOP_SERVICES = '''\
from django.db import models

from .models import Order, Refund


def create_order(customer, total):
    order = Order(customer=customer, total=total)
    order.status = "open"
    order.save()
    return order


def bulk_import_orders(rows):
    Order.objects.bulk_create([Order(customer=r["c"], total=r["t"]) for r in rows])


def issue_refund(order, amount):
    refund = Refund.objects.create(order=order, amount=amount)
    refund.processed = True
    refund.save()
    return refund


def read_order(pk):
    # read-only: never a writer of any table
    return Order.objects.get(pk=pk)
'''

_MODELS = '''\
from django.db import models


class Order(models.Model):
    customer = models.CharField(max_length=200)
    total = models.IntegerField()
    status = models.CharField(max_length=32, default="new")


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    amount = models.IntegerField()
    processed = models.BooleanField(default=False)
'''


def _build_repo(root: pathlib.Path) -> pathlib.Path:
    shop = root / "shop"
    shop.mkdir(parents=True, exist_ok=True)
    (shop / "__init__.py").write_text("")
    (shop / "models.py").write_text(_MODELS)
    (shop / "services.py").write_text(_SHOP_SERVICES)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "init"],
        check=True,
    )
    return root


def test_django_who_writes_resolves_actual_target_model(tmp_path: pathlib.Path) -> None:
    """who_writes('order') and who_writes('refund') must be DISJOINT on the real Django
    app — each names only the functions that actually write its table, through the real
    tool minted by run_full_pipeline."""
    from services.code_intel import orm
    from services.code_intel.pipeline import run_full_pipeline

    repo = _build_repo(tmp_path / "shop-app")
    assert orm.is_tier1(repo) is True, "real Django stack must be tier-1"

    pipeline = run_full_pipeline(tenant_id="t-django-target", repo_url=str(repo))
    server = pipeline.server
    assert server is not None, "run_full_pipeline must attach the real MCP server"

    order_ids = {w.id for w in server.who_writes("order").writers}
    refund_ids = {w.id for w in server.who_writes("refund").writers}

    # create_order + bulk_import_orders write Order; issue_refund does NOT.
    assert "shop/services.py::create_order" in order_ids, order_ids
    assert "shop/services.py::bulk_import_orders" in order_ids, order_ids
    assert "shop/services.py::issue_refund" not in order_ids, (
        f"issue_refund confidently mis-named as an Order writer: {order_ids}"
    )

    # issue_refund writes Refund; create_order / bulk_import_orders do NOT.
    assert "shop/services.py::issue_refund" in refund_ids, refund_ids
    assert "shop/services.py::create_order" not in refund_ids, (
        f"create_order confidently mis-named as a Refund writer: {refund_ids}"
    )
    assert "shop/services.py::bulk_import_orders" not in refund_ids, refund_ids

    # The two writer sets are strictly disjoint — no over-attribution.
    assert order_ids.isdisjoint(refund_ids), (
        f"writer sets must be disjoint; overlap={order_ids & refund_ids}"
    )

    # read-only get() is never a writer of either table.
    assert "shop/services.py::read_order" not in order_ids
    assert "shop/services.py::read_order" not in refund_ids

    # Exact-supported stack: every writer tagged resolved.
    for w in [*server.who_writes("order").writers, *server.who_writes("refund").writers]:
        assert w.confidence == "resolved", f"{w.id} tagged {w.confidence!r}"
