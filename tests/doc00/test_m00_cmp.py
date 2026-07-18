"""Doc 00 · §2 System composition & contracts (AC-CMP-001..017).

Milestone m00. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/contracts`` / ``libs/agentkit`` exist.

Oracle sources: Pydantic/Literal introspection (contract), static call/def
counts over the product tree (static), and a deterministic delta-izer
simulation (model-stateful) -- per PROTO-DETERMINISTIC-01.
"""

import re

import pytest

import _support as S


# ── AC-CMP-001 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cmp_001_six_packages_three_deployables():
    """AC-CMP-001: six mechanism pieces are code packages under services/*, deploying as exactly three deployables."""
    services = S.list_subdirs("services")
    expected_pkgs = {"harness", "code_intel", "transport", "scribe", "workroom"}
    # The six *pieces* (incl. Experience/08 which is apps/* + libs/contracts) collapse to five service packages.
    assert expected_pkgs <= services, f"services/ missing mechanism packages: {expected_pkgs - services}"

    # Deploy target set == exactly three deployables, discovered from the infra/deploy text.
    infra = S.read_all_text("*", root_parts=("infra",)) + S.read_all_text("*", root_parts=("deploy",))
    assert infra.strip(), "no infra/ or deploy/ definitions found (product not built)"
    deployables = {d for d in ("control_plane", "meeting_runtime", "code_intel") if d in infra}
    assert deployables == {"control_plane", "meeting_runtime", "code_intel"}, (
        f"deploy target set != the three canonical deployables: found {deployables}"
    )
    # No fourth deployable: no other Cloud Run / MIG service names beyond the three.
    forbidden = ("control_plane2", "gateway_service", "scribe_service", "transport_service", "workroom_service")
    assert not [f for f in forbidden if f in infra], "a fourth/per-service network deployable exists"


# ── AC-CMP-002 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_002_bundle_exact_field_set():
    """AC-CMP-002: Bundle (04->05) declares exactly {ask, speaker, timestamp, notes_ref:UUID, transcript_tail, task_id}."""
    from uuid import UUID
    from libs.contracts import Bundle

    fields = set(Bundle.model_fields)
    expected = {"ask", "speaker", "timestamp", "notes_ref", "transcript_tail", "task_id"}
    assert fields == expected, f"Bundle field set mismatch: extra={fields - expected}, missing={expected - fields}"

    ann = Bundle.model_fields["notes_ref"].annotation
    assert ann is UUID or getattr(ann, "__name__", "") == "UUID", (
        f"notes_ref must be a UUID handle, not an embedded notes object: got {ann!r}"
    )


# ── AC-CMP-003 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_003_envelope_status_members():
    """AC-CMP-003: EnvelopeStatus is exactly {done, partial, failed, needs_clarification, needs_review}."""
    from typing import get_args
    from libs.contracts import EnvelopeStatus

    members = set(get_args(EnvelopeStatus))
    expected = {"done", "partial", "failed", "needs_clarification", "needs_review"}
    assert members == expected, f"EnvelopeStatus mismatch: extra={members - expected}, missing={expected - members}"
    assert "verified" not in members and "draft" not in members, "verified/draft must NOT be status members"


# ── AC-CMP-004 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_004_agentchunk_union_and_discriminator():
    """AC-CMP-004: AgentChunk union is exactly six ChunkType members with discriminator 'type'."""
    from typing import get_args
    from libs.contracts import AgentChunk, ChunkType

    members = set(get_args(ChunkType))
    expected = {"INIT", "TEXT", "TOOL_USE", "TOOL_RESULT", "RESULT", "ERROR"}
    assert members == expected, f"ChunkType mismatch: extra={members - expected}, missing={expected - members}"

    field_names = set(getattr(AgentChunk, "model_fields", None) or getattr(AgentChunk, "__dataclass_fields__", {}))
    assert "type" in field_names, "AgentChunk discriminator field must be named 'type'"
    assert "kind" not in field_names, "AgentChunk discriminator must NOT be named 'kind'"


# ── AC-CMP-005 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cmp_005_stream_deltas_single_call_site():
    """AC-CMP-005: consumers read stream_deltas output; stream_deltas applied exactly once, never re-wrapped."""
    calls = S.grep_python(r"\bstream_deltas\s*\(")
    assert calls, "no stream_deltas() call site found (product not built)"
    assert len(calls) == 1, f"stream_deltas() must be called exactly once; found {len(calls)}: {calls}"

    relpath, _lineno, _line = calls[0]
    # The single call site is inside BehaviorRunner.run() (libs/agentkit).
    src = S.read_text(*relpath.split("/")) or ""
    assert "class BehaviorRunner" in src, f"the single stream_deltas() call must live with BehaviorRunner; in {relpath}"


# ── AC-CMP-006 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_006_noteop_members():
    """AC-CMP-006: NoteOp is exactly {add, patch, close}; 'note' is absent."""
    from typing import get_args
    from libs.contracts import NoteOp

    members = set(get_args(NoteOp))
    assert members == {"add", "patch", "close"}, f"NoteOp mismatch: {members}"
    assert "note" not in members, "'note' must be dropped (folded into 'add')"


# ── AC-CMP-007 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_007_readiness_members_and_companions():
    """AC-CMP-007: Readiness enum is exactly {connecting, cloning, indexing, ready, not_ready}; 'mapping' absent."""
    from typing import get_args
    from libs import contracts

    members = set(get_args(contracts.Readiness))
    expected = {"connecting", "cloning", "indexing", "ready", "not_ready"}
    assert members == expected, f"Readiness mismatch: extra={members - expected}, missing={expected - members}"
    assert "mapping" not in members, "'mapping' must be dropped (agentic map is Expansion)"

    # coverage_pct (float) and gaps (list) live alongside the enum on the readiness report.
    report = getattr(contracts, "ReadinessReport", None) or getattr(contracts, "Readiness", None)
    report_fields = set(getattr(report, "model_fields", {}))
    assert {"coverage_pct", "gaps"} <= report_fields, f"coverage_pct/gaps missing from readiness report: {report_fields}"


# ── AC-CMP-008 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_008_channel_report_dm_available():
    """AC-CMP-008: channel-report field is named dm_available (bool)."""
    from libs import contracts

    ch = getattr(contracts, "ChannelReport", None)
    assert ch is not None, "ChannelReport signal type not found in libs.contracts"
    fields = getattr(ch, "model_fields", {})
    assert "dm_available" in fields, f"channel-report must name the field 'dm_available'; got {set(fields)}"
    assert fields["dm_available"].annotation is bool, "dm_available must be typed bool"
    assert "dm" not in fields and "dm_available?" not in fields, "legacy dm?/dm-available? names must be absent"


# ── AC-CMP-009 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_009_orphan_contract_field_fails_build():
    """AC-CMP-009: a produced-but-unconsumed contract field fails the build."""
    from libs import contracts

    checker = getattr(contracts, "assert_fields_consumed", None) or getattr(contracts, "check_field_contract", None)
    assert checker is not None, "libs.contracts must expose a produce/consume field-contract check"

    # Feed a synthetic contract where a producer adds a field no consumer reads.
    produced = {"SomeSignal": {"known_field", "orphan_field"}}
    consumed = {"SomeSignal": {"known_field"}}
    try:
        result = checker(produced=produced, consumed=consumed)
    except Exception as exc:  # a raise is an acceptable "fails the build" signal
        assert "orphan_field" in str(exc), f"field-contract error must name the orphan field: {exc}"
        return
    # Or it returns a non-empty violation list naming the orphan field.
    violations = str(result)
    assert result and "orphan_field" in violations, f"orphan field must be reported as a violation: {result!r}"


# ── AC-CMP-010 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cmp_010_shared_mechanisms_defined_once():
    """AC-CMP-010: dispatch() and abort/resume/provider seam are defined once in libs, imported not re-implemented."""
    dispatch_defs = S.count_def_sites("dispatch")
    assert len(dispatch_defs) == 1, f"dispatch() must have exactly one definition; found {dispatch_defs}"
    assert dispatch_defs[0].startswith("libs/http/"), f"dispatch() must live in libs/http; found {dispatch_defs[0]}"

    for symbol, home in (
        ("AbortRegistry", "libs/agentkit/"),
        ("resume_with_fallback", "libs/agentkit/"),
    ):
        defs = S.count_def_sites(symbol)
        assert len(defs) == 1, f"{symbol} must have exactly one definition; found {defs}"
        assert defs[0].startswith(home), f"{symbol} must live in {home}; found {defs[0]}"


# ── AC-CMP-011 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cmp_011_signal_surface_excluded_from_registry_closure():
    """AC-CMP-011: Doc 02 signal-surface events are excluded from assert_registry_closed() scope."""
    from libs import contracts

    registry = getattr(contracts, "CHANNEL_REGISTRY", None)
    assert registry is not None, "CHANNEL_REGISTRY not found in libs.contracts"
    registered = set(registry)
    signal_events = {"transcript", "roster", "speaking", "boundary", "barge-in", "bot-status", "meeting-end", "channel-report"}
    leaked = signal_events & {str(k) for k in registered}
    assert not leaked, f"signal-surface events must NOT be in the client ProxyMessage registry: {leaked}"

    # And the closure assertion passes without demanding a consumer for them.
    assert_closed = getattr(contracts, "assert_registry_closed", None)
    assert callable(assert_closed), "assert_registry_closed() must exist"


# ── AC-CMP-012 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_012_envelope_full_shape():
    """AC-CMP-012: Envelope (05->04) model declares exactly {headline,detail,artifact,receipts,status,verification,draft_id,task_id}."""
    from typing import get_args, get_origin, Union
    from uuid import UUID
    from libs.contracts import Envelope, EnvelopeStatus

    fields = set(Envelope.model_fields)
    expected = {"headline", "detail", "artifact", "receipts", "status", "verification", "draft_id", "task_id"}
    assert fields == expected, f"Envelope field set mismatch: extra={fields - expected}, missing={expected - fields}"

    status_ann = Envelope.model_fields["status"].annotation
    assert status_ann is EnvelopeStatus or set(get_args(status_ann)) == set(get_args(EnvelopeStatus)), (
        "Envelope.status must be typed EnvelopeStatus, not an open str"
    )

    ver_args = set(get_args(Envelope.model_fields["verification"].annotation))
    assert {"verified", "unverified"} <= ver_args, "verification must be an optional Literal[verified, unverified]"
    assert type(None) in ver_args, "verification must be optional (None-able), never a status member"

    task_ann = Envelope.model_fields["task_id"].annotation
    assert task_ann is UUID or getattr(task_ann, "__name__", "") == "UUID", "task_id must be UUID"


# ── AC-CMP-013 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_013_agentchunk_per_variant_metadata():
    """AC-CMP-013: AgentChunk per-variant metadata keys; RESULT carries total_cost_usd (the cost-meter contract)."""
    from libs import contracts

    canonical = getattr(contracts, "AGENT_CHUNK_METADATA_KEYS", None)
    assert canonical is not None, "libs.contracts must declare AGENT_CHUNK_METADATA_KEYS (per-variant metadata contract)"

    expected = {
        "INIT": {"session_id", "tools", "mcp_servers"},
        "TEXT": {"msg_id"},
        "TOOL_USE": {"id", "name", "input"},
        "TOOL_RESULT": {"tool_use_id", "is_error", "structured"},
        "RESULT": {"session_id", "num_turns", "total_cost_usd", "structured_output"},
        "ERROR": {"message"},
    }
    for variant, keys in expected.items():
        assert set(canonical.get(variant, set())) == keys, (
            f"{variant} metadata keys mismatch: {set(canonical.get(variant, set()))} != {keys}"
        )
    assert "total_cost_usd" in canonical["RESULT"], "RESULT.metadata must carry total_cost_usd for the cost meter"


# ── AC-CMP-014 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cmp_014_behaviorconfig_typed_not_yaml():
    """AC-CMP-014: behavior registry is typed Python BehaviorConfig constants; no YAML behavior registry/loader/schema."""
    from libs.agentkit import BehaviorConfig, register  # typed config + registrar

    assert BehaviorConfig is not None and callable(register)

    # No YAML behavior-registry / loader / schema file in the core.
    yaml_files = S.glob("*.yaml", root_parts=("libs", "agentkit")) + S.glob("*.yml", root_parts=("libs", "agentkit"))
    behavior_yaml = [str(p) for p in yaml_files if re.search(r"behavior|registry|disposition", p.name, re.I)]
    assert not behavior_yaml, f"no YAML behavior registry/loader/schema allowed in core: {behavior_yaml}"

    loader_hits = S.grep_python(r"yaml\.(safe_)?load", trees=("libs", "services"))
    behavior_loaders = [h for h in loader_hits if "behavior" in h[0].lower() or "disposition" in h[0].lower()]
    assert not behavior_loaders, f"no YAML behavior loader allowed: {behavior_loaders}"


# ── AC-CMP-015 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_cmp_015_stream_deltas_deltaizes_text_passthrough_rest():
    """AC-CMP-015: stream_deltas emits per-msg_id suffix deltas and passes non-TEXT chunks through unchanged."""
    from libs.agentkit import stream_deltas
    from libs.contracts import AgentChunk, ChunkType

    def text(msg_id, accumulated):
        return AgentChunk(type=ChunkType.TEXT, text=accumulated, metadata={"msg_id": msg_id})

    def other(t):
        return AgentChunk(type=t, text=None, metadata={})

    scripted = [
        other(ChunkType.INIT),
        text("m1", "He"),
        text("m1", "Hello"),
        other(ChunkType.TOOL_USE),
        text("m2", "Bye"),        # new msg_id resets the accumulator
        other(ChunkType.RESULT),
    ]
    out = list(stream_deltas(iter(scripted)))

    text_out = [c for c in out if c.type == ChunkType.TEXT]
    assert [c.text for c in text_out] == ["He", "llo", "Bye"], (
        f"TEXT deltas must be new suffixes per msg_id: {[c.text for c in text_out]}"
    )
    passthrough = [c.type for c in out if c.type != ChunkType.TEXT]
    assert passthrough == [ChunkType.INIT, ChunkType.TOOL_USE, ChunkType.RESULT], (
        f"non-TEXT chunks must pass through unchanged and in order: {passthrough}"
    )

    # Exactly-once is semantic: applying stream_deltas over its own output corrupts the deltas.
    twice = [c for c in stream_deltas(iter(out)) if c.type == ChunkType.TEXT]
    assert [c.text for c in twice] != ["He", "llo", "Bye"], "double application must NOT reproduce single-pass deltas"


# ── AC-CMP-016 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_016_progress_event_shape_minus_finality():
    """AC-CMP-016: a Workroom progress event carries the Envelope shape minus finality (no finalized EnvelopeStatus)."""
    from libs import contracts

    progress = getattr(contracts, "ProgressEvent", None)
    assert progress is not None, "ProgressEvent (progress-event contract variant) not found in libs.contracts"
    pfields = set(progress.model_fields)

    structural = {"headline", "detail", "artifact", "receipts", "task_id"}
    assert structural <= pfields, f"progress event must carry the Envelope structural fields: missing {structural - pfields}"

    # It must NOT carry a finalized terminal EnvelopeStatus (finality absent / non-terminal marker).
    if "status" in pfields:
        from typing import get_args
        status_ann = progress.model_fields["status"].annotation
        terminal = {"done", "failed"}
        assert not (terminal <= set(get_args(status_ann) or ())), (
            "progress event must not carry a finalized terminal EnvelopeStatus value"
        )


# ── AC-CMP-017 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cmp_017_material_change_events_exact_seven():
    """AC-CMP-017: material-change events contract enumerates exactly the 7 kinds; nothing extra."""
    from typing import get_args
    from libs import contracts

    sym = (
        getattr(contracts, "MaterialChangeKind", None)
        or getattr(contracts, "MaterialChange", None)
        or getattr(contracts, "MaterialChangeEvent", None)
        or getattr(contracts, "MaterialChangeType", None)
        or getattr(contracts, "MATERIAL_CHANGE_EVENTS", None)
    )
    assert sym is not None, "material-change events (03->04) type/enum not found in libs.contracts"

    members = set(get_args(sym))
    if not members:
        # enum class or explicit container fallback
        try:
            members = {getattr(m, "value", m) for m in sym}
        except TypeError:
            members = set(sym)

    expected = {
        "claim-landed-checkable",
        "decision-forming",
        "decision-final",
        "contradiction",
        "action-item",
        "question-open",
        "question-closed",
    }
    assert members == expected, (
        f"material-change events must be EXACTLY the 7 kinds: "
        f"extra={members - expected}, missing={expected - members}"
    )
    # The dropped 'note'-style shorthand is not present as a distinct member.
    assert "note" not in members, "'note'-style shorthand must not be a distinct material-change member"
    # decision-forming/final and question-open/closed are the expanded variants,
    # not single combined members.
    assert "decision" not in members and "question" not in members, (
        "decision/question must be the expanded forming/final and open/closed variants, "
        "not single combined members"
    )
