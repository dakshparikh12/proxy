"""Reality + negative tiers for the quality gate (vendor:anthropic).

These drive Proxy's REAL ``call_external`` seam against a *recorded* Anthropic
response (a vcrpy cassette): the real Haiku entailment call and the real Sonnet
escalation call. NO cassette exists this session, so each is skipped with an
explicit reason — never faked, never a Mock() standing in for the seam. When a
cassette is recorded, drop the skip and these assert against real client
construction + messages.create round-trip through the seam.

Covers the REALITY rung of AC-QGATE-02/-03/-04/-09/-10/-11 and the NEGATIVE rung of
AC-QGATE-07-NEG / -08-NEG / -09-NEG / -10-NEG / -11-NEG.
"""
from __future__ import annotations

import pytest

_NO_CASSETTE = "reality tier: no anthropic cassette this session"


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_02_reality_decision_final_entailment_over_real_seam() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_03_reality_irreversible_entailment_over_real_seam() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_04_reality_contradicts_entailment_over_real_seam() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_09_reality_miss_escalates_to_real_sonnet_over_same_window() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_10_reality_sonnet_correction_applied_as_attributed_patch() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE)
def test_qgate_11_reality_miss_record_written_to_transcript_plane() -> None:
    raise AssertionError("unreached: skipped pending cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_qgate_07neg_reality_call_uses_resolved_model_not_fallback() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_qgate_08neg_reality_grounded_true_no_escalation() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_qgate_09neg_reality_grounded_true_zero_sonnet_calls() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_qgate_10neg_reality_empty_sonnet_result_no_patch() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")


@pytest.mark.skip(reason=_NO_CASSETTE + " (negative cassette)")
def test_qgate_11neg_reality_double_failure_still_logs_miss() -> None:
    raise AssertionError("unreached: skipped pending negative cassette")
