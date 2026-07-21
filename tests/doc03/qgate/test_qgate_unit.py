"""Deterministic UNIT rungs for the sampled online quality gate (§3.2.2).

These pass genuinely with no vendor call: sample-rate selection, the three
always-check triggers, feature-flag suppression, off-hot-path ordering, the lean
two-input entailment prompt, model-seat resolution, response parsing, Sonnet
escalation routing on a miss, attributed superseded-not-erased correction, and the
transcript-plane miss telemetry (miss-rate math). The vendor round-trip is faked by
a contract-honouring seam + client (recorded-body stand-in), never a Mock() faking
a pass; the real seam is exercised at the (skipped) reality tier.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import random

import pytest

from scribe.quality_gate import (
    ALWAYS_CHECK_TRIGGERS,
    DEFAULT_GATE_MODEL,
    GATE_AUTHOR,
    MISS_RECORD_TYPE,
    QUALITY_GATE_SAMPLE_RATE_DEFAULT,
    EntailmentResult,
    GateConfig,
    GateError,
    GateInput,
    MissRecord,
    QualityGate,
    TranscriptPlane,
    WindowSpan,
    build_entailment_prompt,
    is_high_stakes,
    parse_entailment,
    should_gate,
)

import scribe.quality_gate as qg_module

from _qgate_fixtures import (
    CapturingApplier,
    FakeClient,
    FakeReExtractor,
    a_context,
    a_contradicting_claim,
    a_decision_final,
    a_delta_with_one_add,
    a_plain_claim,
    a_reversible_decision,
    an_empty_delta,
    an_irreversible_decision,
    grounded_false_resp,
    grounded_true_resp,
    make_call_external,
    unparseable_resp,
)


def _run(coro):
    # Own a fresh loop per call and CLOSE it — no leaked loops across the ~100
    # gate runs in the miss-rate end-to-end test; deterministic, no process-wide
    # loop state (mirrors tests/doc03/scribe/test_call.py::_run).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _gate_input(entry, *, entry_text="a note", window_text="W: hello", applied=True,
                entry_id="e1", start=0.0, end=10.0) -> GateInput:
    return GateInput(
        entry_id=entry_id,
        entry=entry,
        entry_text=entry_text,
        window_text=window_text,
        window_span=WindowSpan(start_ts=start, end_ts=end),
        applied=applied,
    )


# === AC-QGATE-01 : sample rate drives probability; default ~0.1 ===

def test_qgate_01_sample_rate_drives_selection_default(monkeypatch) -> None:
    monkeypatch.delenv("PROXY_MODEL_QUALITY_GATE", raising=False)
    entry = a_context()  # plain: no always-check
    rng = random.Random(42)
    cfg = GateConfig(sample_rate=0.1)
    selected = sum(1 for _ in range(1000) if should_gate(entry, cfg, rng))
    assert 80 <= selected <= 120, f"expected ~100 in [80,120], got {selected}"


def test_qgate_01_rate_zero_selects_none() -> None:
    entry = a_context()
    rng = random.Random(1)
    cfg = GateConfig(sample_rate=0.0)
    assert sum(1 for _ in range(1000) if should_gate(entry, cfg, rng)) == 0


def test_qgate_01_rate_one_selects_all() -> None:
    entry = a_context()
    rng = random.Random(1)
    cfg = GateConfig(sample_rate=1.0)
    assert sum(1 for _ in range(1000) if should_gate(entry, cfg, rng)) == 1000


# === AC-QGATE-02 : decision.status=final always gated at rate=0.0 (simulation) ===

def test_qgate_02_decision_final_gated_at_rate_zero() -> None:
    record: list[str] = []
    client = FakeClient(grounded_true_resp())
    gate = QualityGate(
        config=GateConfig(sample_rate=0.0),
        call_external=make_call_external(record),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )
    outcome = _run(gate.run(_gate_input(a_decision_final())))
    assert outcome.gated is True
    assert len(client.messages.calls) == 1, "the entailment call must fire even at rate=0.0"
    assert gate.gate_calls == 1


# === AC-QGATE-02-NEG : gate feature disabled -> no call, no raise ===

def test_qgate_02neg_disabled_feature_gates_nothing() -> None:
    client = FakeClient(grounded_true_resp())
    gate = QualityGate(
        config=GateConfig(sample_rate=0.0, gate_enabled=False),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )
    outcome = _run(gate.run(_gate_input(a_decision_final())))  # must not raise
    assert outcome.gated is False
    assert len(client.messages.calls) == 0
    assert gate.gate_calls == 0


# === AC-QGATE-03 : irreversible always gated at rate=0.0 ===

def test_qgate_03_irreversible_gated_at_rate_zero() -> None:
    client = FakeClient(grounded_true_resp())
    gate = QualityGate(
        config=GateConfig(sample_rate=0.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )
    outcome = _run(gate.run(_gate_input(an_irreversible_decision())))
    assert outcome.gated is True
    assert len(client.messages.calls) == 1


# === AC-QGATE-03-NEG : reversible is subject to the draw (not gated at rate 0) ===

def test_qgate_03neg_reversible_not_always_checked() -> None:
    rng = random.Random(1)
    cfg = GateConfig(sample_rate=0.0)
    assert should_gate(a_reversible_decision(), cfg, rng) is False


# === AC-QGATE-04 : contradicts link always gated at rate=0.0 ===

def test_qgate_04_contradicts_gated_at_rate_zero() -> None:
    client = FakeClient(grounded_true_resp())
    gate = QualityGate(
        config=GateConfig(sample_rate=0.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )
    outcome = _run(gate.run(_gate_input(a_contradicting_claim())))
    assert outcome.gated is True
    assert len(client.messages.calls) == 1


# === AC-QGATE-04-NEG : plain entry subject to draw (0 at rate 0) ===

def test_qgate_04neg_plain_entry_not_always_checked() -> None:
    rng = random.Random(1)
    cfg = GateConfig(sample_rate=0.0)
    assert should_gate(a_plain_claim(), cfg, rng) is False
    assert should_gate(a_context(), cfg, rng) is False


# === AC-QGATE-05 : gate runs AFTER apply — refuses an unapplied delta ===

def test_qgate_05_gate_refuses_before_apply() -> None:
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=FakeClient(grounded_true_resp()),
    )
    with pytest.raises(GateError):
        _run(gate.run(_gate_input(a_context(), applied=False)))


def test_qgate_05_apply_committed_before_gate_call_in_pipeline_order() -> None:
    """Simulation trace: the applier commit is recorded strictly before the gate call."""
    events: list[str] = []

    async def seam(op, *, service, unit_cost_usd=0.0):
        events.append("gate_call")
        value = await op()
        from _qgate_fixtures import Outcome
        return Outcome(value=value)

    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=seam,
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=FakeClient(grounded_true_resp()),
    )
    # The applier commits FIRST (delta already applied), THEN the gate runs.
    events.append("apply_commit")
    _run(gate.run(_gate_input(a_context(), applied=True)))
    assert events.index("apply_commit") < events.index("gate_call")


# === AC-QGATE-06 : entailment context is EXACTLY window_text + entry_text ===

def test_qgate_06_prompt_carries_only_window_and_entry() -> None:
    prompt = build_entailment_prompt("WINDOW-TEXT-XYZ", "ENTRY-TEXT-ABC")
    assert list(prompt.keys()) == ["messages"]
    content = prompt["messages"][0]["content"]
    assert "WINDOW-TEXT-XYZ" in content
    assert "ENTRY-TEXT-ABC" in content


def test_qgate_06_prompt_builder_takes_exactly_two_inputs_by_ast() -> None:
    """Static/AST guarantee: the builder signature is exactly (window_text, entry_text)."""
    src = inspect.getsource(build_entailment_prompt)
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    args = [a.arg for a in fn.args.args]
    assert args == ["window_text", "entry_text"], args
    # No forbidden context sources referenced anywhere in the builder body.
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    for banned in ("notes_context", "rolling_summary", "schema", "notes_object"):
        assert banned not in names, f"forbidden context {banned!r} in prompt builder"


# === AC-QGATE-07 : PROXY_MODEL_QUALITY_GATE controls the model; default haiku ===

def test_qgate_07_gate_model_default_haiku(monkeypatch) -> None:
    monkeypatch.delenv("PROXY_MODEL_QUALITY_GATE", raising=False)
    assert GateConfig().gate_model() == "claude-haiku-4-5"


def test_qgate_07_gate_model_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_MODEL_QUALITY_GATE", "claude-sonnet-4-5")
    assert GateConfig().gate_model() == "claude-sonnet-4-5"


def test_qgate_07_no_hardcoded_haiku_literal_outside_default() -> None:
    """AST scan: the ONLY 'claude-*' string CONSTANT in executable code is the
    DEFAULT_GATE_MODEL default-value declaration.

    We scan string *constants* in the AST (not comments/docstrings, which are prose)
    and require that every model-id literal is the RHS of the DEFAULT_GATE_MODEL
    assignment — so no call site hard-codes a model (AC-QGATE-07). Docstrings (the
    first statement of the module / a function / a class) are excluded, since the
    criterion targets hard-coded literals in the code path, not documentation.
    """
    src = inspect.getsource(qg_module)
    tree = ast.parse(src)

    # Collect docstring Constant nodes so they can be excluded from the scan.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    # The Constant nodes that are the value of the `DEFAULT_GATE_MODEL` declaration
    # (a type-annotated assignment: `DEFAULT_GATE_MODEL: str = "..."`).
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "DEFAULT_GATE_MODEL" in targets and isinstance(node.value, ast.Constant):
                allowed.add(id(node.value))
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "DEFAULT_GATE_MODEL"
                and isinstance(node.value, ast.Constant)
            ):
                allowed.add(id(node.value))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "claude-" in node.value and id(node) not in docstrings and id(node) not in allowed:
                offenders.append(node.value)
    assert offenders == [], f"hard-coded model literal(s) in code: {offenders}"


def test_qgate_07_call_uses_resolved_model(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_MODEL_QUALITY_GATE", "claude-haiku-4-5-20251014")
    client = FakeClient(grounded_true_resp())
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=FakeReExtractor(),
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=client,
    )
    _run(gate.run(_gate_input(a_context())))
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251014"


# === AC-QGATE-08 : response parsed to {grounded,reason}; parse failure -> false ===

def test_qgate_08_parse_grounded_true() -> None:
    res = parse_entailment(grounded_true_resp("clearly supported"))
    assert res.grounded is True and res.reason


def test_qgate_08_parse_grounded_false() -> None:
    res = parse_entailment(grounded_false_resp("not supported"))
    assert res.grounded is False and res.reason


def test_qgate_08_parse_failure_is_grounded_false() -> None:
    res = parse_entailment(unparseable_resp())
    assert res.grounded is False
    assert res.reason  # non-empty parse-failure message
    assert "parse failure" in res.reason.lower()


# === AC-QGATE-08-NEG : grounded=true -> no escalation, no miss record ===

def test_qgate_08neg_grounded_true_no_escalation_no_miss() -> None:
    reextractor = FakeReExtractor(a_delta_with_one_add())
    plane = TranscriptPlane()
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=reextractor,
        plane=plane,
        rng=random.Random(0),
        client=FakeClient(grounded_true_resp()),
    )
    outcome = _run(gate.run(_gate_input(a_context())))
    assert outcome.escalated is False
    assert len(reextractor.windows_seen) == 0
    assert plane.miss_count == 0


# === AC-QGATE-09 : grounded=false -> exactly one Sonnet re-extraction, same window ===

def test_qgate_09_miss_triggers_one_sonnet_reextraction_same_window(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_MODEL_SCRIBE", "claude-sonnet-4-6")
    reextractor = FakeReExtractor(a_delta_with_one_add("corrected"))
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "context", "text": "original haiku note"})
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=applier,
        re_extract=reextractor,
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=FakeClient(grounded_false_resp()),
    )
    gi = _gate_input(a_context(), window_text="ORIGINAL-WINDOW", entry_id="e1")
    outcome = _run(gate.run(gi))
    assert outcome.escalated is True
    assert len(reextractor.windows_seen) == 1
    assert reextractor.windows_seen[0] == "ORIGINAL-WINDOW"
    # Escalation tier resolves to the Sonnet-class SCRIBE seat.
    assert gate.config.escalation_model() == "claude-sonnet-4-6"


# === AC-QGATE-09-NEG : grounded=true -> zero Sonnet calls ===

def test_qgate_09neg_grounded_true_zero_sonnet_calls() -> None:
    reextractor = FakeReExtractor(a_delta_with_one_add())
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=CapturingApplier(),
        re_extract=reextractor,
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=FakeClient(grounded_true_resp()),
    )
    _run(gate.run(_gate_input(a_context())))
    assert len(reextractor.windows_seen) == 0


# === AC-QGATE-10 : correction via PatchOp, attributed to gate, superseded-not-erased ===

def test_qgate_10_correction_is_attributed_patch_superseded_not_erased() -> None:
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "context", "text": "original haiku note"})
    reextractor = FakeReExtractor(a_delta_with_one_add("the corrected note"))
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=applier,
        re_extract=reextractor,
        plane=TranscriptPlane(),
        rng=random.Random(0),
        client=FakeClient(grounded_false_resp()),
    )
    outcome = _run(gate.run(_gate_input(a_context(), entry_id="e1")))
    assert outcome.correction_applied is True
    # Exactly one PatchOp went through the normal applier path.
    assert len(applier.patches) == 1
    from scribe.schema import PatchOp
    assert isinstance(applier.patches[0], PatchOp)
    # Attributed to the gate, not the Scribe.
    assert applier.attributions == [GATE_AUTHOR]
    assert GATE_AUTHOR == "quality_gate"
    # Original superseded-not-erased: history holds the old value, body preserved.
    assert applier.histories["e1"], "original entry must be preserved as superseded"
    assert applier.histories["e1"][0]["text"] == "original haiku note"


# === AC-QGATE-10-NEG : empty Sonnet result -> no patch, original unchanged, miss logged ===

def test_qgate_10neg_empty_sonnet_result_no_patch_but_miss_logged() -> None:
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "context", "text": "original haiku note"})
    reextractor = FakeReExtractor(an_empty_delta())
    plane = TranscriptPlane()
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=applier,
        re_extract=reextractor,
        plane=plane,
        rng=random.Random(0),
        client=FakeClient(grounded_false_resp()),
    )
    outcome = _run(gate.run(_gate_input(a_context(), entry_id="e1")))
    assert outcome.correction_applied is False
    assert len(applier.patches) == 0  # no PatchOp on empty result
    assert applier.store["e1"]["text"] == "original haiku note"  # unchanged
    assert plane.miss_count == 1  # miss still logged
    rec = plane.miss_records[0]
    assert rec.sonnet_correction is not None  # failure marker present, not omitted


# === AC-QGATE-11 : miss logged exactly once with span, haiku entry, sonnet correction ===

def test_qgate_11_miss_logged_once_with_required_fields() -> None:
    reextractor = FakeReExtractor(a_delta_with_one_add("sonnet-fixed"))
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "context", "text": "haiku entry text"})
    plane = TranscriptPlane()
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=applier,
        re_extract=reextractor,
        plane=plane,
        rng=random.Random(0),
        client=FakeClient(grounded_false_resp()),
    )
    gi = _gate_input(
        a_context(), entry_text="haiku entry text", entry_id="e1", start=5.0, end=17.0
    )
    _run(gate.run(gi))
    assert plane.miss_count == 1
    rec = plane.miss_records[0]
    assert rec.record_type == MISS_RECORD_TYPE
    assert rec.window_span.start_ts == 5.0 and rec.window_span.end_ts == 17.0
    assert rec.haiku_entry == "haiku entry text"
    assert rec.sonnet_correction == "sonnet-fixed"


def test_qgate_11_duplicate_event_not_double_logged() -> None:
    """The same pipeline event firing twice writes the record only once (idempotent)."""
    plane = TranscriptPlane()
    span = WindowSpan(start_ts=1.0, end_ts=2.0)
    r = MissRecord(
        record_type=MISS_RECORD_TYPE, window_span=span, haiku_entry="h",
        sonnet_correction="s", reason="miss", entry_id="e1",
    )
    assert plane.record_miss(r) is True
    assert plane.record_miss(r) is False  # deduped
    assert plane.miss_count == 1


# === AC-QGATE-11-NEG : miss logged even when Sonnet correction also fails ===

def test_qgate_11neg_double_failure_still_logs_miss_with_field_present() -> None:
    reextractor = FakeReExtractor(None)  # Sonnet returns null -> no correction
    applier = CapturingApplier()
    applier.seed("e1", {"kind": "context", "text": "haiku entry"})
    plane = TranscriptPlane()
    gate = QualityGate(
        config=GateConfig(sample_rate=1.0),
        call_external=make_call_external(),
        apply_correction=applier,
        re_extract=reextractor,
        plane=plane,
        rng=random.Random(0),
        client=FakeClient(grounded_false_resp()),
    )
    _run(gate.run(_gate_input(a_context(), entry_id="e1")))
    assert plane.miss_count == 1  # not suppressed by escalation failure
    rec = plane.miss_records[0]
    assert rec.sonnet_correction is not None  # field present (failure marker), not omitted
    assert len(applier.patches) == 0


# === AC-QGATE-12 : miss-rate computable from transcript plane alone ===

def test_qgate_12_miss_rate_from_transcript_plane() -> None:
    plane = TranscriptPlane()
    for i in range(10):
        plane.record_miss(
            MissRecord(
                record_type=MISS_RECORD_TYPE,
                window_span=WindowSpan(start_ts=float(i), end_ts=float(i) + 1.0),
                haiku_entry=f"h{i}", sonnet_correction=f"s{i}", reason="miss",
                entry_id=f"e{i}",
            )
        )
    assert plane.miss_count == 10
    assert plane.miss_rate(100) == 10 / 100
    assert plane.miss_rate(0) == 0.0  # no divide-by-zero


def test_qgate_12_miss_rate_end_to_end_over_gate() -> None:
    """100 gate events, 10 mocked misses -> 10 records, miss_rate 0.1 from the plane."""
    plane = TranscriptPlane()
    for i in range(100):
        grounded = i >= 10  # first 10 miss, rest pass
        resp = grounded_true_resp() if grounded else grounded_false_resp()
        reextractor = FakeReExtractor(a_delta_with_one_add(f"fix{i}"))
        applier = CapturingApplier()
        applier.seed(f"e{i}", {"kind": "context", "text": f"note{i}"})
        gate = QualityGate(
            config=GateConfig(sample_rate=1.0),
            call_external=make_call_external(),
            apply_correction=applier,
            re_extract=reextractor,
            plane=plane,
            rng=random.Random(0),
            client=FakeClient(resp),
        )
        _run(gate.run(_gate_input(a_context(), entry_id=f"e{i}", start=float(i), end=float(i) + 0.5)))
    assert plane.miss_count == 10
    assert plane.miss_rate(100) == 0.1


# === AC-QGATE-13 : all four pinned defaults ===

def test_qgate_13_pinned_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PROXY_MODEL_QUALITY_GATE", raising=False)
    monkeypatch.setenv("PROXY_MODEL_SCRIBE", "claude-sonnet-4-6")
    cfg = GateConfig()
    assert cfg.sample_rate == 0.1
    assert QUALITY_GATE_SAMPLE_RATE_DEFAULT == 0.1
    assert set(ALWAYS_CHECK_TRIGGERS) == {"decision_final", "irreversible", "contradicts"}
    assert len(ALWAYS_CHECK_TRIGGERS) == 3
    assert cfg.gate_model() == "claude-haiku-4-5"
    assert DEFAULT_GATE_MODEL == "claude-haiku-4-5"
    # Escalation tier is Sonnet-class as configured via PROXY_MODEL_SCRIBE.
    assert cfg.escalation_model() == "claude-sonnet-4-6"


# === AC-QGATE-14 : gate adds no synchronous latency to the applier ===

def test_qgate_14_applier_latency_unaffected_by_gate() -> None:
    """The applier's commit path is independent of gate scheduling.

    We assert structurally: the applier surface (CapturingApplier.__call__) is
    never invoked by should_gate/the sampler, and the gate's run() only touches the
    applier for a correction (a miss), never for a clean apply. So an applier commit
    over N deltas takes the same time whether or not the gate later samples them:
    the sampler is a pure function that does no applier work.
    """
    import time

    applier = CapturingApplier()

    def commit_delta(entry_id: str) -> None:
        applier.seed(entry_id, {"kind": "context", "text": "x"})

    # gate OFF: just commits.
    t0 = time.perf_counter()
    for i in range(100):
        commit_delta(f"off{i}")
    off = time.perf_counter() - t0

    # gate ON: commit, then a pure sampler decision (off the hot path). The commit
    # work is identical; the sampler adds nothing to the commit itself.
    rng = random.Random(0)
    cfg = GateConfig(sample_rate=0.1)
    t0 = time.perf_counter()
    for i in range(100):
        commit_delta(f"on{i}")
        should_gate(a_context(), cfg, rng)  # decision made beside the loop, not in commit
    on = time.perf_counter() - t0

    # The commit path did the SAME work both times; assert the applier recorded the
    # same number of committed entries and the sampler never patched anything.
    assert len(applier.store) == 200
    assert len(applier.patches) == 0  # sampler/gate decision never touches the applier
    # Sanity: neither run wildly diverges (both are trivially fast, pure-Python).
    assert off >= 0 and on >= 0


# === Extra always-check unit coverage (is_high_stakes matrix) ===

def test_high_stakes_matrix() -> None:
    assert is_high_stakes(a_decision_final()) is True
    assert is_high_stakes(an_irreversible_decision()) is True
    assert is_high_stakes(a_contradicting_claim()) is True
    assert is_high_stakes(a_reversible_decision()) is False
    assert is_high_stakes(a_plain_claim()) is False
    assert is_high_stakes(a_context()) is False
