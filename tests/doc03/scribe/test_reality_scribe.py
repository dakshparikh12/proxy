\
"""Reality + negative tiers for the Scribe micro-call (vendor:anthropic).

These drive Proxy's REAL ``call_external`` seam against a *recorded* Anthropic
response (a vcrpy cassette). NO cassette exists for the Scribe this session, so each
is skipped with an explicit reason — never faked, never a Mock() standing in for the
seam. When a cassette is recorded, drop the skip and these assert against real client
construction + messages.create round-trip.

Covers REALITY rung of AC-SCRIBE-01, -02, -13, -15 and NEGATIVE rung of
AC-SCRIBE-01-NEG, -02-NEG, -13-NEG, -15-NEG.
"""
from __future__ import annotations

import pytest

_NO_CASSETTE = "reality tier: no anthropic cassette this session"


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_scribe_01_reality_one_call_structured_delta_out() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_scribe_02_reality_two_breakpoints_over_real_seam() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_scribe_13_reality_injection_recorded_as_claim_not_obeyed() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_scribe_15_reality_latency_p50_and_cache_read() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_scribe_01neg_reality_5xx_degrades_honestly() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_scribe_02neg_reality_error_no_partial_delta() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_scribe_13neg_reality_injection_path_degrades_honestly() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_scribe_15neg_reality_latency_path_degrades_honestly() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")
