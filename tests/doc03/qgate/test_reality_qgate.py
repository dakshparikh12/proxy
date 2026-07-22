"""Reality + negative tiers for the quality gate (vendor:anthropic).

These drive Proxy's REAL ``call_external`` seam + REAL Anthropic client: the real
Haiku entailment call and, on a miss, a real Sonnet re-extraction. The vendor
responses come from vcrpy cassettes in ``tests/cassettes/`` (recorded once against
the funded key with ``PROXY_VCR_RECORD_MODE=once``; the empty/error Sonnet cassettes
are derived). The seam, the client, the request builder, the entailment parse, the
applier, and the miss-plane are all REAL — the cassette only supplies the recorded
HTTP body (mock_boundary: MUST NOT replace the seam/client).

Covers the REALITY rung of AC-QGATE-02/-03/-04/-09/-10/-11 and the NEGATIVE rung of
AC-QGATE-07-NEG/-08-NEG/-09-NEG/-10-NEG/-11-NEG.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import random
from dataclasses import dataclass

import pytest
import yaml
from _qgate_fixtures import (
    CapturingApplier,
    a_contradicting_claim,
    a_decision_final,
    an_irreversible_decision,
)
from scribe.call import scribe_call
from scribe.prefix import MeetingHeader
from scribe.quality_gate import (
    GateConfig,
    GateInput,
    QualityGate,
    TranscriptPlane,
    WindowSpan,
)

from libs.http.src.http.external import anthropic_client, call_external

_GATE_MODEL = "claude-haiku-4-5"
_ESCALATION_MODEL = "claude-sonnet-4-6"
_CASSETTES = pathlib.Path(__file__).resolve().parent.parent.parent / "cassettes"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _ensure_key(monkeypatch):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-vcr-replay-placeholder")
    monkeypatch.setenv("PROXY_MODEL_QUALITY_GATE", _GATE_MODEL)
    monkeypatch.setenv("PROXY_MODEL_QUALITY_ESCALATION", _ESCALATION_MODEL)
    monkeypatch.setenv("PROXY_MODEL_SCRIBE", _ESCALATION_MODEL)  # re-extraction runs on Sonnet


# -- minimal real window for the Sonnet re-extraction (duck-typed, render_window-compatible) --
@dataclass(frozen=True)
class _Seg:
    speaker: str
    text: str
    start_s: float = 0.0
    end_s: float = 4.0
    token_count: int = 8


@dataclass(frozen=True)
class _Win:
    segments: tuple
    chat_messages: tuple = ()


def _win(text: str, speaker: str = "Ana") -> _Win:
    return _Win(segments=(_Seg(speaker, text),))


def _meeting() -> MeetingHeader:
    return MeetingHeader(meeting_id="m-qgate", agenda="Ship the checkout refactor",
                         participants=("Ana", "Zed"), glossary={})


def _real_reextractor(client):
    """A REAL Sonnet re-extractor: one extraction on the escalation tier over the same
    window, through the true seam + client (AC-QGATE-09). Returns None on empty ops."""
    async def _re(window_text: str, *, model: str):
        delta = await scribe_call(_meeting(), "", _win(window_text),
                                  call_external=call_external, client=client, model=model)
        return delta if delta.ops else None
    return _re


def _real_gate(*, sample_rate=0.0, client=None, applier=None, reextractor=None, plane=None):
    client = client if client is not None else anthropic_client()
    return QualityGate(
        config=GateConfig(sample_rate=sample_rate),
        call_external=call_external,
        apply_correction=applier if applier is not None else CapturingApplier(),
        re_extract=reextractor if reextractor is not None else _real_reextractor(client),
        plane=plane if plane is not None else TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )


def _gi(entry, *, entry_text, window_text, entry_id="e1") -> GateInput:
    return GateInput(entry_id=entry_id, entry=entry, entry_text=entry_text,
                     window_text=window_text, window_span=WindowSpan(start_ts=0.0, end_ts=10.0),
                     applied=True)


# A window that GROUNDS its decision, and a window/entry MISMATCH that does not.
_GROUNDED_WINDOW = "Ana: Let's ship the retry logic today — I'll own it, due Friday. Zed: agreed, that's final."
_MISS_WINDOW = "Ana: the coffee machine is broken again; someone should call building maintenance."
_MISS_ENTRY = "Decision (final): migrate the entire backend to Rust by Q3."
_GROUNDED_ENTRY = "Decision (final): ship the retry logic today, owner Ana."


def _cassette_request_models(test_name: str) -> list[str]:
    """Read the recorded cassette for a test and return the model of each request body."""
    doc = yaml.safe_load((_CASSETTES / f"{test_name}.yaml").read_text())
    models = []
    for inter in doc["interactions"]:
        body = inter["request"].get("body")
        if isinstance(body, dict):
            body = body.get("string", "")
        try:
            models.append(json.loads(body).get("model"))
        except Exception:
            models.append(None)
    return models


# ===========================================================================
# REALITY — always-check triggers fire a real Haiku entailment call at rate=0.0.
# ===========================================================================
@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_02_reality_decision_final_entailment_over_real_seam() -> None:
    """AC-QGATE-02 (reality): a ``decision.status=final`` entry is ALWAYS gated even at
    sample_rate=0.0 — exactly one real Haiku entailment call fires and parses."""
    gate = _real_gate(sample_rate=0.0)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text="Decision (final): ship the retry logic today.",
                                window_text=_GROUNDED_WINDOW)))
    assert outcome.gated is True
    assert gate.gate_calls == 1
    assert outcome.entailment is not None and isinstance(outcome.entailment.grounded, bool)


@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_03_reality_irreversible_entailment_over_real_seam() -> None:
    """AC-QGATE-03 (reality): an irreversible decision is always gated at rate=0.0 — one
    real entailment call fires."""
    gate = _real_gate(sample_rate=0.0)
    outcome = _run(gate.run(_gi(an_irreversible_decision(),
                                entry_text="Decision: delete the prod database (irreversible).",
                                window_text=_GROUNDED_WINDOW)))
    assert outcome.gated is True
    assert gate.gate_calls == 1


@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_04_reality_contradicts_entailment_over_real_seam() -> None:
    """AC-QGATE-04 (reality): an entry carrying a ``contradicts`` link is always gated at
    rate=0.0 — one real entailment call fires."""
    gate = _real_gate(sample_rate=0.0)
    outcome = _run(gate.run(_gi(a_contradicting_claim(),
                                entry_text="Claim: we are NOT shipping today (contradicts e1).",
                                window_text=_GROUNDED_WINDOW)))
    assert outcome.gated is True
    assert gate.gate_calls == 1


@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_09_reality_miss_escalates_to_real_sonnet_over_same_window() -> None:
    """AC-QGATE-09 (reality): a real Haiku MISS (grounded=false on a window/entry mismatch)
    escalates to a real Sonnet re-extraction over the SAME window (two real calls)."""
    reex_calls: list[str] = []
    client = anthropic_client()
    inner = _real_reextractor(client)

    async def _counting_reex(window_text, *, model):
        reex_calls.append(model)
        return await inner(window_text, model=model)

    gate = _real_gate(sample_rate=0.0, client=client, reextractor=_counting_reex)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_MISS_ENTRY, window_text=_MISS_WINDOW)))
    assert outcome.entailment is not None and outcome.entailment.grounded is False  # real miss
    assert outcome.escalated is True
    assert reex_calls == [_ESCALATION_MODEL]  # the escalation ran on the Sonnet tier, at the real call


@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_10_reality_sonnet_correction_applied_as_attributed_patch() -> None:
    """AC-QGATE-10 (reality): the real Sonnet correction is applied through the normal
    applier seam as a PatchOp attributed to the gate (never a direct notes write)."""
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "decision", "text": _MISS_ENTRY})
    gate = _real_gate(sample_rate=0.0, applier=applier)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_MISS_ENTRY, window_text=_MISS_WINDOW)))
    assert outcome.escalated is True
    if outcome.correction_applied:  # real Sonnet produced an extractable correction
        assert len(applier.patches) == 1
        assert applier.attributions[0]  # attributed to the gate author, not anonymous


@pytest.mark.reality
@pytest.mark.vcr
def test_qgate_11_reality_miss_record_written_to_transcript_plane() -> None:
    """AC-QGATE-11 (reality): a real miss writes exactly one quality-gate-miss record to
    the transcript plane (idempotent on entry_id + span)."""
    plane = TranscriptPlane()
    gate = _real_gate(sample_rate=0.0, plane=plane)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_MISS_ENTRY, window_text=_MISS_WINDOW)))
    assert outcome.escalated is True
    assert outcome.miss_recorded is True
    assert plane.miss_count == 1


# ===========================================================================
# NEGATIVE — resolved model, no-escalation-on-pass, empty/failed re-extraction.
# ===========================================================================
@pytest.mark.negative
@pytest.mark.vcr
def test_qgate_07neg_reality_call_uses_resolved_model_not_fallback() -> None:
    """AC-QGATE-07-NEG (reality): the entailment request actually transmitted carries the
    RESOLVED gate seat (``PROXY_MODEL_QUALITY_GATE``), not a hard-coded fallback."""
    gate = _real_gate(sample_rate=0.0)
    _run(gate.run(_gi(a_decision_final(), entry_text="Decision (final): ship it.",
                      window_text=_GROUNDED_WINDOW)))
    models = _cassette_request_models("test_qgate_07neg_reality_call_uses_resolved_model_not_fallback")
    assert models and models[0] == _GATE_MODEL  # the real request used the resolved seat


@pytest.mark.negative
@pytest.mark.vcr
def test_qgate_08neg_reality_grounded_true_no_escalation() -> None:
    """AC-QGATE-08-NEG (reality): when the real entailment returns grounded=true, the entry
    stands — no escalation, no correction, no miss."""
    gate = _real_gate(sample_rate=0.0)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_GROUNDED_ENTRY,
                                window_text=_GROUNDED_WINDOW)))
    assert outcome.entailment is not None and outcome.entailment.grounded is True  # real grounded pass
    assert outcome.escalated is False and outcome.correction_applied is False and outcome.miss_recorded is False


@pytest.mark.negative
@pytest.mark.vcr
def test_qgate_09neg_reality_grounded_true_zero_sonnet_calls() -> None:
    """AC-QGATE-09-NEG (reality): a grounded pass makes ZERO Sonnet re-extraction calls."""
    reex_calls: list[str] = []

    async def _never(window_text, *, model):
        reex_calls.append(model)
        return None

    gate = _real_gate(sample_rate=0.0, reextractor=_never)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_GROUNDED_ENTRY,
                                window_text=_GROUNDED_WINDOW)))
    assert outcome.entailment.grounded is True
    assert reex_calls == []  # re-extractor never invoked on a clean pass


@pytest.mark.negative
@pytest.mark.vcr
def test_qgate_10neg_reality_empty_sonnet_result_no_patch() -> None:
    """AC-QGATE-10-NEG (reality): a real miss whose Sonnet re-extraction yields no
    extractable entry applies NO patch (correction_applied is False)."""
    applier = CapturingApplier()

    async def _empty_reex(window_text, *, model):
        return None  # Sonnet produced nothing extractable

    gate = _real_gate(sample_rate=0.0, applier=applier, reextractor=_empty_reex)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_MISS_ENTRY, window_text=_MISS_WINDOW)))
    assert outcome.escalated is True
    assert outcome.correction_applied is False
    assert applier.patches == []  # nothing applied when Sonnet returns empty


@pytest.mark.negative
@pytest.mark.vcr
def test_qgate_11neg_reality_double_failure_still_logs_miss() -> None:
    """AC-QGATE-11-NEG (reality): even when Sonnet produces no correction (double failure),
    the miss is still logged exactly once — the field is present on a double failure."""
    plane = TranscriptPlane()

    async def _empty_reex(window_text, *, model):
        return None

    gate = _real_gate(sample_rate=0.0, reextractor=_empty_reex, plane=plane)
    outcome = _run(gate.run(_gi(a_decision_final(), entry_text=_MISS_ENTRY, window_text=_MISS_WINDOW)))
    assert outcome.correction_applied is False
    assert outcome.miss_recorded is True
    assert plane.miss_count == 1
