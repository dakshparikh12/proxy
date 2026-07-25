"""AC-INV-007 (strengthened): the per-meeting cache key MUST be tenant-scoped.

The sealed ``test_ac_inv_007_meeting_cache_keys_are_tenant_scoped`` in
``tests/test_invariants.py`` uses two SEPARATE ``MeetingSession`` instances, so
each has its own ``_cache`` dict — it therefore cannot detect a cross-tenant
*collision* on a shared cache. This module adds the stronger oracle: it shares
ONE cache boundary across two tenants and proves tenant-B never receives
tenant-A's cached value.

The regression it pins (node ``codeintel.tenant_isolation``): ``MeetingSession``
keyed the cache on ``(tool, sorted(args))`` with the tenant_id OMITTED, so two
tenants issuing the identical ``(tool, args)`` call through the same cache would
collide. These tests FAIL against that omit-tenant_id code and pass once the key
incorporates the bound server's ``tenant_id``.

Fixture signal (``TwoTenantCloneFixture`` + ``for_tenant``):
  tenant-A ``get_dependents("some_fn")`` -> ``[table::a_only]``
  tenant-B ``get_dependents("some_fn")`` -> ``[table::b_only]``
so a cross-tenant cache hit is directly observable (tenant-B would see
``table::a_only``).
"""
from __future__ import annotations

import pytest

from services.code_intel.mcp_server import CodeIntelMCPServer
from services.code_intel.meeting import MeetingSession
from tests.fixtures.stubs import TwoTenantCloneFixture


@pytest.mark.smoke
def test_shared_session_cache_never_serves_cross_tenant_value() -> None:
    """One MeetingSession + one _cache dict, driven as tenant-A then tenant-B.

    Priming the cache as tenant-A then issuing the identical call as tenant-B
    (by rebinding the same session's server to tenant-B) must recompute against
    tenant-B's graph — not return tenant-A's cached ``[table::a_only]``.

    Against the omit-tenant_id key this FAILS: the ``(get_dependents, [(limit,50),
    (symbol,some_fn)])`` key hits tenant-A's cached entry and tenant-B receives
    ``table::a_only`` (a cross-tenant leak).
    """
    fixture = TwoTenantCloneFixture()
    tenant_a_ids = fixture.tenant_a_node_ids
    tenant_b_ids = fixture.tenant_b_node_ids

    server_a = CodeIntelMCPServer.for_tenant("tenant-A", fixture=fixture)
    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)

    # ONE session, ONE cache dict — the shared boundary the sealed test lacked.
    session = MeetingSession(server=server_a, pipeline=server_a.pipeline)

    # Prime the shared cache as tenant-A.
    result_a = session.tool_call("get_dependents", symbol="some_fn", limit=50)
    a_ids = {item.id for item in result_a.results}
    assert a_ids == {"table::a_only"}, f"tenant-A should see its own node, got {a_ids}"

    # Rebind the SAME session (SAME _cache) to tenant-B and issue the identical call.
    session._server = server_b
    session._pipeline = server_b.pipeline
    result_b = session.tool_call("get_dependents", symbol="some_fn", limit=50)
    b_ids = {item.id for item in result_b.results}

    leaked = b_ids & tenant_a_ids
    assert leaked == set(), (
        f"cross-tenant cache collision: tenant-B received tenant-A cached nodes {leaked} "
        f"(cache key omits tenant_id)"
    )
    assert b_ids.issubset(tenant_b_ids), (
        f"tenant-B result must contain only tenant-B nodes, got {b_ids}"
    )
    assert b_ids == {"table::b_only"}, (
        f"tenant-B must be recomputed against its own graph, got {b_ids}"
    )


@pytest.mark.smoke
def test_cache_key_is_tenant_scoped_no_key_collision() -> None:
    """The computed cache key itself must differ per tenant for the identical call.

    A white-box guard on the key: two tenant-bound sessions computing the key for
    the SAME (tool, args) must not produce the SAME key — otherwise a shared cache
    would collide. This fails if tenant_id is absent from the key tuple.
    """
    fixture = TwoTenantCloneFixture()
    server_a = CodeIntelMCPServer.for_tenant("tenant-A", fixture=fixture)
    server_b = CodeIntelMCPServer.for_tenant("tenant-B", fixture=fixture)

    session_a = MeetingSession(server=server_a, pipeline=server_a.pipeline)
    session_b = MeetingSession(server=server_b, pipeline=server_b.pipeline)

    args = {"symbol": "some_fn", "limit": 50}
    key_a = session_a._cache_key("get_dependents", args)
    key_b = session_b._cache_key("get_dependents", args)

    assert key_a != key_b, (
        "cache key omits tenant_id: identical (tool, args) collides across tenants "
        f"(both keyed as {key_a!r})"
    )
    assert "tenant-A" in repr(key_a) and "tenant-B" in repr(key_b), (
        f"tenant_id must appear in the cache key ({key_a!r} / {key_b!r})"
    )
