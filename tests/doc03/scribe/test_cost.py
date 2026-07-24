"""AC-COST-01/02/03/03-NEG — the Scribe per-call cost is computed from token usage.

Gap DOC03-SCRIBE-COST-COMPUTATION-ABSENT: the four Haiku rate constants and the
``scribe_call_cost_usd(usage)`` function that turns a response usage object into a
per-call USD figure did not exist, so a real Scribe window recorded $0 model cost
(silent under-reporting of the cost ledger). These tests pin the exact rate
constants and the arithmetic (AC-COST-01/02/03/03-NEG), and prove the REAL
``scribe.call.scribe_call`` path derives and reports that cost from the response
``usage`` — no manual injection of a cost figure.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scribe.cost import (
    HAIKU_CACHE_READ,
    HAIKU_CACHE_WRITE,
    HAIKU_INPUT,
    HAIKU_OUTPUT,
    scribe_call_cost_usd,
)

from ._fixtures import (
    FakeClient,
    FakeResp,
    ToolUseBlock,
    a_meeting,
    a_valid_delta_input,
    a_window,
    make_call_external,
)


# -- AC-COST-01: the four rate constants are the exact values --
def test_ac_cost_01_rate_constants_are_exact() -> None:
    assert HAIKU_INPUT == 1.00e-6
    assert HAIKU_OUTPUT == 5.00e-6
    assert abs(HAIKU_CACHE_WRITE - 1.25e-6) < 1e-15
    assert abs(HAIKU_CACHE_READ - 1.00e-7) < 1e-15
    assert abs(HAIKU_CACHE_WRITE - HAIKU_INPUT * 1.25) < 1e-15
    assert abs(HAIKU_CACHE_READ - HAIKU_INPUT * 0.10) < 1e-15


# -- AC-COST-02: correct total from all four usage fields (first-call scenario) --
def test_ac_cost_02_total_from_all_four_fields() -> None:
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_creation_input_tokens=800,
        cache_read_input_tokens=0,
    )
    result = scribe_call_cost_usd(usage)
    assert abs(result - 0.003000) < 1e-12


# -- AC-COST-03: no AttributeError when cache fields are absent --
def test_ac_cost_03_absent_cache_fields_default_to_zero() -> None:
    usage = SimpleNamespace(input_tokens=500, output_tokens=100)
    result = scribe_call_cost_usd(usage)
    assert result > 0.0
    assert abs(result - (500 * HAIKU_INPUT + 100 * HAIKU_OUTPUT)) < 1e-12


# -- AC-COST-03-NEG: explicit-zero cache tokens contribute exactly $0 --
def test_ac_cost_03_neg_zero_cache_tokens_only_input_output() -> None:
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=100,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    result = scribe_call_cost_usd(usage)
    assert abs(result - (500 * HAIKU_INPUT + 100 * HAIKU_OUTPUT)) < 1e-12


# -- WIRED: the REAL scribe_call derives and reports cost from resp.usage --
@pytest.mark.asyncio
async def test_scribe_call_reports_cost_from_real_usage() -> None:
    from scribe.call import scribe_call

    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_creation_input_tokens=800,
        cache_read_input_tokens=0,
    )
    resp = FakeResp(content=[ToolUseBlock(input=a_valid_delta_input())], usage=usage)
    client = FakeClient(resp)

    reported: list[float] = []

    async def on_usage(u: object) -> None:
        reported.append(scribe_call_cost_usd(u))

    delta = await scribe_call(
        a_meeting(),
        "",
        a_window(),
        call_external=make_call_external(),
        client=client,
        on_usage=on_usage,
    )
    assert delta is not None
    assert reported == [pytest.approx(0.003000, abs=1e-12)]


@pytest.mark.asyncio
async def test_scribe_call_without_on_usage_is_a_noop() -> None:
    from scribe.call import scribe_call

    resp = FakeResp(
        content=[ToolUseBlock(input=a_valid_delta_input())],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
    )
    delta = await scribe_call(
        a_meeting(), "", a_window(), call_external=make_call_external(), client=FakeClient(resp)
    )
    assert delta is not None
