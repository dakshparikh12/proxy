"""Whole-meeting e2e harness — offline rot-proof + the gated LIVE smoke.

Two tiers, mirroring the batteries' pattern:

* OFFLINE (always runs): the harness modules import cleanly and their pure pieces
  (validation, attribution, deterministic grounding, edge derivation, markdown render)
  work on synthetic data — no subscription, no network, no clone. This keeps the
  harness rot-proof in the default suite.

* LIVE (gated by ``E2E_MEETING_LIVE=1``): the full smoke — prime cal.com, generate one
  meeting on the subscription, play it real-time on the REAL engine, monitor + judge,
  write the readable trace. E2B is provisioned when ``E2E_MEETING_LIVE_E2B=1`` (needs
  ``E2B_API_KEY``). Env knobs: ``E2E_MEETING_REPO`` (default calcom), ``E2E_MEETING_TYPE``
  (default technical), ``E2E_MEETING_COMPRESSION`` (default 0.04).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.eval.generate_meetings import MeetingLine, PlantedAsk, validate_meeting_dict
from tests.eval.meeting_monitor import EdgeReport, edges_from_traces
from tests.eval.plan_trace import TraceEvent, TurnTrace


def _valid_meeting_dict() -> dict:
    """A minimal valid generated-meeting dict (for the offline validator rot-proof)."""
    lines = [{"ts": float(i), "speaker": "Ada", "text": f"line {i}"} for i in range(45)]
    asks = []
    # Plant the required kinds + nuances on distinct lines.
    # (id, kind, nuance, follow_up, follow_up_ts, transport_verbs, cant_do)
    plan = [
        ("a1", "big-coding-task", "", None, None, [], False),
        ("a2", "pr-draft", "", None, None, [], False),
        ("a3", "mute", "", None, None, ["mute"], False),
        ("a4", "easy", "", None, None, [], True),   # honest can't-do (regular kind + cant_do)
        ("a5", "grounded-lookup", "clarify", "yes the first one", 20.0, [], False),
        ("a6", "research", "moved-on", None, None, [], False),
        ("a7", "grounded-lookup", "barge", None, None, [], False),
        ("a8", "easy", "", None, None, [], False),
        ("a9", "verification", "", None, None, [], False),
        ("a10", "debug", "", None, None, [], False),
        ("a11", "multi-file-lookup", "", None, None, [], False),
        ("a12", "post-chat", "", None, None, ["post_chat"], False),
        ("a13", "consolidation", "", None, None, [], False),
    ]
    for i, (aid, kind, nuance, fu, fu_ts, verbs, cant) in enumerate(plan):
        ts = float(i + 1)
        text = f"proxy, please handle task {aid} in detail on the codebase"
        lines[i + 1] = {"ts": ts, "speaker": "Ada", "text": text}
        asks.append({
            "id": aid, "kind": kind, "ts": ts, "speaker": "Ada", "ask": text,
            "gold": f"Proxy should correctly handle {kind} ask {aid} with grounded evidence.",
            "nuance": nuance, "follow_up": fu, "follow_up_ts": fu_ts,
            "require_transport": verbs, "cant_do": cant,
        })
    return {"title": "Test", "participants": ["Ada", "Ben"], "lines": lines, "asks": asks}


def test_meeting_validator_accepts_a_complete_meeting() -> None:
    errors = validate_meeting_dict(_valid_meeting_dict())
    assert errors == [], f"a complete meeting should validate clean, got: {errors}"


def test_meeting_validator_flags_missing_nuances_and_kinds() -> None:
    d = _valid_meeting_dict()
    # Neutralize the barge nuance and the pr-draft kind (keep the ask count >=12 so the
    # nuance/kind checks actually run rather than short-circuiting on the count floor).
    for a in d["asks"]:
        if a["nuance"] == "barge":
            a["nuance"] = ""
        if a["kind"] == "pr-draft":
            a["kind"] = "easy"
    errors = validate_meeting_dict(d)
    assert any("barge" in e for e in errors), errors
    assert any("pr-draft" in e for e in errors), errors


def _synthetic_trace(*, addressed: str, tools: list[str], cost: float | None = 0.01) -> TurnTrace:
    t0 = time.perf_counter()
    tr = TurnTrace(prompt=f"...You were addressed:\n{addressed}", t_start=t0)
    tr.note_text("m1", "on it")
    tr.events.append(TraceEvent(kind="TEXT", t=t0 + 0.1, text="on it"))
    for i, name in enumerate(tools):
        tr.events.append(TraceEvent(kind="TOOL_USE", t=t0 + 0.2 + i * 0.1, name=name,
                                    input={"q": "x"}, call_id=f"c{i}"))
    meta = {"num_turns": 1}
    if cost is not None:
        meta["total_cost_usd"] = cost
    tr.result_meta = meta
    tr.t_end = t0 + 1.0
    return tr


def test_edge_derivation_reports_all_edges() -> None:
    traces = [
        _synthetic_trace(addressed="proxy do x", tools=["mcp__code_intel__grep"]),
        _synthetic_trace(addressed="proxy do y", tools=["mcp__sandbox__run_command"]),
        _synthetic_trace(addressed="proxy do z", tools=["mcp__drafts__propose_change"]),
    ]
    rep = edges_from_traces(traces)
    assert rep.claude and rep.code_server and rep.sandbox_e2b and rep.drafts
    assert rep.code_calls == 1 and rep.sandbox_calls == 1 and rep.draft_calls == 1


def test_attribution_maps_turns_to_asks() -> None:
    from tests.eval.judge_meeting import attribute_traces

    asks = [
        PlantedAsk(id="a1", kind="easy", ts=1.0, speaker="Ada",
                   ask="proxy what is retry backoff", gold="x" * 40),
        PlantedAsk(id="a2", kind="easy", ts=2.0, speaker="Ada",
                   ask="proxy where is auth", gold="x" * 40),
    ]
    traces = [
        _synthetic_trace(addressed="proxy what is retry backoff", tools=[]),
        _synthetic_trace(addressed="proxy where is auth", tools=[]),
    ]
    by = attribute_traces(traces, asks)
    assert len(by["a1"]) == 1 and len(by["a2"]) == 1


def test_deterministic_grounding_resolves_real_paths(tmp_path: Path) -> None:
    from tests.eval.judge_meeting import deterministic_checks

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("\n".join(f"line {i}" for i in range(30)))
    asks = [
        PlantedAsk(id="ok", kind="grounded-lookup", ts=1.0, speaker="A",
                   ask="proxy where is login", gold="x" * 40),
        PlantedAsk(id="bad", kind="grounded-lookup", ts=2.0, speaker="A",
                   ask="proxy where is signup", gold="x" * 40),
    ]
    good = TurnTrace(prompt="You were addressed:\nproxy where is login", t_start=0.0)
    good.note_text("m", "It's in src/auth.py:12 near the token check.")
    bad = TurnTrace(prompt="You were addressed:\nproxy where is signup", t_start=0.0)
    bad.note_text("m", "See src/nonexistent.py:999 for the flow.")
    checks = deterministic_checks(
        asks=asks, ask_traces={"ok": [good], "bad": [bad]}, clone_path=tmp_path,
        staged_draft_summaries=[], transport_calls=[],
    )
    by = {c.ask_id: c for c in checks}
    assert by["ok"].grounding_clean and "src/auth.py:12" in by["ok"].resolved
    assert not by["bad"].grounding_clean and by["bad"].unresolved


def test_world_touch_gate_flags_missing_draft() -> None:
    from tests.eval.judge_meeting import deterministic_checks

    asks = [PlantedAsk(id="pr", kind="pr-draft", ts=1.0, speaker="A",
                       ask="proxy open a PR to fix the retry", gold="x" * 40)]
    # A pr-draft turn that staged NO draft (only spoke) → gate leak.
    tr = TurnTrace(prompt="You were addressed:\nproxy open a PR to fix the retry", t_start=0.0)
    tr.note_text("m", "Done, I pushed the fix.")
    checks = deterministic_checks(
        asks=asks, ask_traces={"pr": [tr]}, clone_path=Path("/tmp"),
        staged_draft_summaries=[], transport_calls=[],
    )
    assert checks[0].direct_apply_leak and not checks[0].gate_clean


@pytest.mark.skipif(
    os.environ.get("E2E_MEETING_LIVE") != "1",
    reason="the whole-meeting live smoke is founder-gated (set E2E_MEETING_LIVE=1)",
)
def test_e2e_meeting_live_smoke() -> None:
    """The full live smoke — prime, generate, play, monitor, judge, write the trace."""
    import asyncio

    from tests.eval.smoke_meeting import run_smoke

    result = asyncio.run(run_smoke(
        repo=os.environ.get("E2E_MEETING_REPO", "calcom"),
        meeting_type=os.environ.get("E2E_MEETING_TYPE", "technical"),
        compression=float(os.environ.get("E2E_MEETING_COMPRESSION", "0.04")),
        live_e2b=os.environ.get("E2E_MEETING_LIVE_E2B") == "1",
        build_map=os.environ.get("E2E_MEETING_MAP", "1") == "1",
        meeting_file=os.environ.get("E2E_MEETING_FILE") or None,
    ))
    print(f"\n=== TRACE: {result.trace_path} ===")
    print(f"=== EDGES: {result.edges.summary()} ===")
    passed = sum(1 for j in result.judgements if j.passed)
    print(f"=== JUDGE: {passed}/{len(result.judgements)} pass; arc {result.arc.score:.2f} ===")
    # The smoke's job is to PRODUCE the trace + honest read, not to assert a bar; the
    # only hard requirement is that the real edges fired and the trace was written.
    assert result.trace_path.exists()
    assert result.edges.claude, "Claude edge never fired — the engine did not run"
