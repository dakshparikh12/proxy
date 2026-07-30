"""PLAN-QUALITY + LATENCY battery — the founder's #1 acceptance criterion.

Two tiers in this file:

* **Always-run (offline, hermetic)** — the committed scenario pool
  (``tests/eval/plan_scenarios.json``) parses and satisfies the schema across
  every generatable ask class; the trace capture works end-to-end through the
  REAL Engine on a scripted plan-shaped provider (no model, no judge, no
  network); the latency math is EXACT on synthetic timelines; the per-class
  bounds table covers every class and flags violations correctly; the sample
  selector is deterministic. Proves the committed machinery isn't rot.

* **Live (gated)** — ``PLAN_QUALITY_LIVE=1`` drives sampled pool scenarios
  through the real engine on the Claude Max subscription (real code tools over
  the committed battery clone, fake recording transport, optional real E2B),
  scores every ask's PLAN + response with G-Eval on the subscription judge,
  prints the per-class/aggregate report, writes the JSON artifact, and asserts
  the deterministic invariants + per-class latency bounds + the judge floor.

Run live (from the repo root):

    PLAN_QUALITY_LIVE=1 SAMPLE=25 .venv/bin/python -m pytest \
        tests/eval/test_plan_quality.py -q -s -k live

Env knobs:
    SAMPLE=N                     class-stratified deterministic subset (default: all)
    PLAN_QUALITY_CLASSES=a,b     restrict to specific ask classes (chunked runs)
    PLAN_QUALITY_LIVE_E2B=1      provision a REAL E2B sandbox for sandbox-exec
                                 asks (needs E2B_API_KEY); RUN_BATTERY_LIVE_E2B=1
                                 is honored too; without either, those asks are
                                 judged on the honest can't-run bar
    PLAN_QUALITY_ARTIFACT_DIR=…  where the JSON artifact lands (default: tempdir)
"""
from __future__ import annotations

import asyncio
import os
import statistics
import tempfile
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.eval.meeting_battery import MODEL
from tests.eval.plan_quality import (
    PlanAskResult,
    build_artifact,
    render_report,
    run_plan_scenario,
    score_results,
    select_sample,
    write_artifact,
)
from tests.eval.plan_trace import (
    LATENCY_BOUNDS,
    TraceEvent,
    TurnMetrics,
    TurnTrace,
    check_bounds,
    derive_metrics,
)
from tests.eval.scenarios_generated import (
    ASK_CLASSES,
    GENERATABLE_CLASSES,
    SCAFFOLDED_CLASSES,
    PlanScenario,
    load_pool,
    validate_scenario_dict,
)

_LIVE = os.environ.get("PLAN_QUALITY_LIVE") == "1"
_LIVE_E2B = (
    os.environ.get("PLAN_QUALITY_LIVE_E2B") == "1"
    or os.environ.get("RUN_BATTERY_LIVE_E2B") == "1"
)


# ── The scripted plan-shaped provider (offline tier only) ─────────────────────


class ScriptedPlanProvider:
    """Replays one plan-shaped happy turn per call: ack TEXT → grep TOOL_USE →
    TOOL_RESULT → answer TEXT → RESULT. Distinct inputs per call so redundancy
    stays zero; records every prompt for attribution checks."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, prompt: str, query: object) -> AsyncIterator[Any]:
        from contracts import AgentChunk

        self.prompts.append(prompt)
        n = len(self.prompts)
        yield AgentChunk(type="TEXT", text="On it.", metadata={"msg_id": f"m{n}"})
        yield AgentChunk(
            type="TOOL_USE",
            text=None,
            metadata={
                "id": f"t{n}",
                "name": "mcp__code_intel__grep",
                "input": {"pattern": f"needle-{n}"},
            },
        )
        yield AgentChunk(
            type="TOOL_RESULT", text="hit", metadata={"tool_use_id": f"t{n}", "is_error": False}
        )
        yield AgentChunk(
            type="TEXT",
            text="On it. MAX_RETRIES is 4 in retry.py.",
            metadata={"msg_id": f"m{n}"},
        )
        yield AgentChunk(
            type="RESULT",
            text="MAX_RETRIES is 4 in retry.py.",
            metadata={"session_id": f"s{n}", "num_turns": 3, "total_cost_usd": 0.0},
        )


# ── Always-run tier 1: the committed pool is valid, broad, and replayable ─────


def test_pool_is_committed_valid_and_covers_every_generatable_class() -> None:
    """The minted pool loads through the strict loader (schema + uniqueness),
    carries the initial ~60+ mint spread over EVERY generatable class, and
    contains nothing for scaffolded classes (no product surface yet)."""
    pool = load_pool()
    assert len(pool) >= 60, f"pool holds {len(pool)} scenarios (initial mint targets 60-100)"
    counts = Counter(s.ask_class for s in pool)
    for cls in GENERATABLE_CLASSES:
        assert counts[cls] >= 5, f"class {cls!r} has only {counts[cls]} scenarios (want >=5)"
    for cls in SCAFFOLDED_CLASSES:
        assert counts[cls] == 0, f"scaffolded class {cls!r} must stay empty until its tools land"


def test_pr_draft_class_is_scaffolded_not_forgotten() -> None:
    """The world-touching PR-draft class exists in the taxonomy + bounds table,
    is excluded from generation, and its TODO names the missing product seam
    (DRAFT_TOOLS) — so it cannot silently vanish from the plan."""
    assert "pr-draft" in ASK_CLASSES
    assert "pr-draft" in SCAFFOLDED_CLASSES
    assert "pr-draft" not in GENERATABLE_CLASSES
    assert "pr-draft" in LATENCY_BOUNDS
    assert "SCAFFOLD" in LATENCY_BOUNDS["pr-draft"].note


def test_validator_rejects_every_class_contract_break() -> None:
    """Each class-specific physics rule is enforced by the validator."""
    base: dict[str, Any] = {
        "id": "x-001",
        "ask_class": "quick-answer",
        "ask": "Proxy, quick one — what did we just decide?",
        "expected_behavior": "Answers directly from the context without tool sprawl; wrong recap fails.",
        "context": [["Ana", "We decided to cap retries at two."]],
    }
    assert validate_scenario_dict(base) == []

    bad = dict(base)
    bad["ask"] = "So what did we decide?"  # no wake name
    assert any("wake name" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["context"] = [["Ana", "Proxy is rejoining now."]]  # context line would wake
    assert any("would wake" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "clarify"  # clarify without the un-prefixed reply
    assert any("follow_up" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "clarify"
    bad["follow_up"] = "Proxy, the burst one."  # reply must be un-prefixed
    assert any("un-prefixed" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "concurrent"  # concurrent without the second ask
    assert any("second_ask" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "meeting-control"  # control without recordable verbs
    assert any("require_transport" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "meeting-control"
    bad["require_transport"] = ["restart_pod"]  # not a transport verb
    assert any("unknown transport verb" in e for e in validate_scenario_dict(bad))

    bad = dict(base)
    bad["ask_class"] = "sandbox-exec"  # exec without the honest no-sandbox bar
    assert any("no_sandbox_behavior" in e for e in validate_scenario_dict(bad))


# ── Always-run tier 2: the latency math is exact on synthetic timelines ───────


def _trace(events: list[TraceEvent], *, t_start: float, t_end: float | None) -> TurnTrace:
    trace = TurnTrace(prompt="p\n\nYou were addressed:\nProxy, x", t_start=t_start, t_end=t_end)
    trace.events = events
    return trace


def test_latency_math_is_exact_on_a_synthetic_timeline() -> None:
    """Every derived number is checked against a hand-computed timeline."""
    t_wake = 100.0
    trace = _trace(
        [
            TraceEvent(kind="TEXT", t=101.5, text="On it."),
            TraceEvent(kind="TOOL_USE", t=103.0, name="grep", input={"q": "a"}),
            TraceEvent(kind="TOOL_RESULT", t=104.0),
            TraceEvent(kind="TOOL_USE", t=106.5, name="grep", input={"q": "a"}),  # redundant
            TraceEvent(kind="TOOL_USE", t=108.0, name="read", input={"f": "x"}),
            TraceEvent(kind="RESULT", t=110.0),
        ],
        t_start=100.2,
        t_end=110.2,
    )
    m = derive_metrics(trace, t_wake=t_wake)
    assert m.ack_latency_s == pytest.approx(1.5)
    assert m.first_tool_latency_s == pytest.approx(3.0)
    assert m.complete_latency_s == pytest.approx(10.2)
    assert m.tool_gaps_s == pytest.approx((3.5, 1.5))
    assert m.tool_count == 3
    assert m.redundant_calls == 1  # same tool+input twice
    assert m.ack_before_work is True  # first TEXT (101.5) < first TOOL_RESULT (104.0)
    assert m.spoke is True
    assert m.tool_sequence == ("grep", "grep", "read")
    assert m.error is None


def test_latency_math_edge_cases() -> None:
    """Never-spoke, work-before-ack, and same-tool-different-input cases."""
    # Never spoke: no ack mark, ack_before_work False.
    silent = derive_metrics(
        _trace([TraceEvent(kind="RESULT", t=105.0)], t_start=100.0, t_end=105.0), t_wake=100.0
    )
    assert silent.ack_latency_s is None
    assert silent.spoke is False
    assert silent.ack_before_work is False

    # The first TOOL_RESULT landed BEFORE the first spoken TEXT → ack-after-work.
    late_ack = derive_metrics(
        _trace(
            [
                TraceEvent(kind="TOOL_USE", t=101.0, name="grep", input={"q": "a"}),
                TraceEvent(kind="TOOL_RESULT", t=102.0),
                TraceEvent(kind="TEXT", t=103.0, text="Found it."),
            ],
            t_start=100.0,
            t_end=104.0,
        ),
        t_wake=100.0,
    )
    assert late_ack.ack_before_work is False
    assert late_ack.spoke is True

    # Same tool, DIFFERENT input → not redundant.
    varied = derive_metrics(
        _trace(
            [
                TraceEvent(kind="TOOL_USE", t=101.0, name="grep", input={"q": "a"}),
                TraceEvent(kind="TOOL_USE", t=102.0, name="grep", input={"q": "b"}),
            ],
            t_start=100.0,
            t_end=103.0,
        ),
        t_wake=100.0,
    )
    assert varied.redundant_calls == 0

    # No clean t_end: the last event's stamp bounds completion honestly.
    unclosed = derive_metrics(
        _trace([TraceEvent(kind="TEXT", t=101.0, text="hi")], t_start=100.0, t_end=None),
        t_wake=100.0,
    )
    assert unclosed.complete_latency_s == pytest.approx(1.0)


def _metrics(**overrides: Any) -> TurnMetrics:
    base: dict[str, Any] = {
        "ack_latency_s": 3.0,
        "first_tool_latency_s": 5.0,
        "complete_latency_s": 30.0,
        "tool_gaps_s": (),
        "tool_count": 1,
        "redundant_calls": 0,
        "ack_before_work": True,
        "spoke": True,
        "tool_sequence": ("mcp__code_intel__grep",),
        "sdk_num_turns": 3,
        "error": None,
    }
    base.update(overrides)
    return TurnMetrics(**base)


def test_bounds_table_covers_every_class_and_flags_violations() -> None:
    """Every ask class carries documented bounds; clean metrics pass; each
    violation kind is flagged with the observed value AND the bound."""
    for cls in ASK_CLASSES:
        assert cls in LATENCY_BOUNDS, f"no bounds entry for class {cls!r}"
        assert LATENCY_BOUNDS[cls].note.strip(), f"bounds for {cls!r} are undocumented"

    assert check_bounds(_metrics(), "quick-answer") == []

    slow = check_bounds(_metrics(ack_latency_s=45.0, complete_latency_s=400.0), "quick-answer")
    assert any("ack latency" in v for v in slow)
    assert any("turn-complete" in v for v in slow)

    sprawl = check_bounds(
        _metrics(tool_count=9, redundant_calls=3, tool_sequence=("g",) * 9), "grounded-lookup"
    )
    assert any("tool calls" in v for v in sprawl)
    assert any("redundant" in v for v in sprawl)

    silent = check_bounds(_metrics(ack_latency_s=None, spoke=False, ack_before_work=False), "cant-do")
    assert any("never spoke" in v for v in silent)

    late = check_bounds(_metrics(ack_before_work=False), "quick-answer")
    assert any("ack-after-work" in v for v in late)
    # meeting-control deliberately does NOT assert ack order (act-then-ack is compliant).
    assert check_bounds(_metrics(ack_before_work=False), "meeting-control") == []

    assert any("unknown ask class" in v for v in check_bounds(_metrics(), "no-such-class"))


# ── Always-run tier 3: capture flows through the REAL engine (scripted) ───────


@pytest.mark.asyncio
async def test_quick_ask_is_traced_through_the_real_engine_offline() -> None:
    """One scripted ask through the real Engine + real code-server construction:
    the tee attributes the turn, captures the ordered plan, and derives sane
    marks — no model, no judge, no network."""
    scenario = PlanScenario(
        id="off-quick",
        ask_class="quick-answer",
        ask="Proxy, what's the max retry count?",
        expected_behavior="States MAX_RETRIES is 4 (retry.py) without tool sprawl.",
        context=(("Ana", "Quick sync on the retry work."),),
    )
    results = await run_plan_scenario(scenario, provider=ScriptedPlanProvider())
    assert len(results) == 1
    r = results[0]
    assert r.woke and r.error is None and r.unexpected_wakes == 0
    assert "MAX_RETRIES" in r.response_text
    m = r.metrics
    assert m is not None
    assert m.tool_sequence == ("mcp__code_intel__grep",)
    assert m.tool_count == 1 and m.redundant_calls == 0
    assert m.spoke and m.ack_before_work is True
    assert m.ack_latency_s is not None and 0.0 <= m.ack_latency_s <= m.complete_latency_s
    assert m.sdk_num_turns == 3
    assert "TOOL_USE" in r.trace_text and "mcp__code_intel__grep" in r.trace_text
    assert r.bound_violations == []


@pytest.mark.asyncio
async def test_concurrent_and_clarify_flows_are_attributed_offline() -> None:
    """Concurrent: two asks, no drain between, two attributed results. Clarify:
    the un-prefixed reply wakes as the follow-up and both turns ride the arc."""
    concurrent = PlanScenario(
        id="off-conc",
        ask_class="concurrent",
        ask="Proxy, first one: what's the session TTL?",
        expected_behavior="Answers 1800 seconds (auth.py) — this question gets a real answer.",
        second_ask="Proxy, second one: what's the redis default TTL?",
        second_expected_behavior="Answers 600 seconds (cache_redis.py) — a real answer too.",
    )
    results = await run_plan_scenario(concurrent, provider=ScriptedPlanProvider())
    assert [r.ask_id for r in results] == ["off-conc", "off-conc-b"]
    assert all(r.woke for r in results)
    assert all(r.metrics is not None for r in results)

    clarify = PlanScenario(
        id="off-clar",
        ask_class="clarify",
        ask="Proxy, what's the cutoff on that cache again?",
        expected_behavior="Opens with the fork (which cache?), then answers the chosen one.",
        follow_up="The in-memory one, the LRU.",
        context=(("Ben", "Both caches looked guilty during the incident."),),
    )
    results = await run_plan_scenario(clarify, provider=ScriptedPlanProvider())
    assert len(results) == 1
    r = results[0]
    assert r.woke and r.follow_up_woke is True
    assert "clarifying turn" in r.trace_text and "after the clarification" in r.trace_text
    assert r.metrics is not None  # the FIRST turn's plan, never diluted by the reply


def test_sample_selection_is_deterministic_and_stratified() -> None:
    def _s(cls: str, i: int) -> PlanScenario:
        return PlanScenario(
            id=f"{cls}-{i}",
            ask_class=cls,
            ask=f"Proxy, {cls} question {i}?",
            expected_behavior="x" * 40,
        )

    pool = [_s("quick-answer", i) for i in range(3)]
    pool += [_s("cant-do", i) for i in range(3)]
    pool += [_s("reconnect", i) for i in range(3)]
    pool += [
        PlanScenario(
            id="pr-draft-0", ask_class="pr-draft", ask="Proxy, draft it?", expected_behavior="x" * 40
        )
    ]

    picked = select_sample(pool, 5)
    assert [s.id for s in picked] == [
        "quick-answer-0", "cant-do-0", "reconnect-0", "quick-answer-1", "cant-do-1",
    ]
    assert picked == select_sample(pool, 5)  # deterministic replay
    assert all(s.ask_class not in SCAFFOLDED_CLASSES for s in select_sample(pool, None))
    only = select_sample(pool, None, classes=["cant-do"])
    assert {s.ask_class for s in only} == {"cant-do"}


def test_report_and_artifact_render_without_a_judge() -> None:
    """The report/artifact path is pure over scored results (fed a hand-built one)."""
    from tests.eval.plan_quality import ScoredAsk

    result = PlanAskResult(
        ask_id="r-1",
        scenario_id="r-1",
        ask_class="quick-answer",
        ask_text="Proxy, quick one?",
        criteria="answers",
        woke=True,
        response_text="Sure — it's four.",
        trace_text="+ 1.00s TEXT 'Sure'",
        metrics=_metrics(),
        transport_verbs=(),
        require_transport=(),
        sandbox_mounted=False,
        unexpected_wakes=0,
        error=None,
        tags=("easy",),
    )
    scored = [ScoredAsk(result=result, score=0.91, reason="clean", passed=True)]
    report = render_report(scored)
    assert "PLAN-QUALITY + LATENCY REPORT" in report and "quick-answer" in report
    artifact = build_artifact(scored, threshold=0.7, model=MODEL, sample_note="unit")
    assert artifact["meta"]["asks"] == 1
    assert artifact["asks"][0]["metrics"]["ack_latency_s"] == 3.0
    with tempfile.TemporaryDirectory() as tmp:
        path = write_artifact(artifact, Path(tmp))
        assert path.exists() and path.suffix == ".json"


# ── The live battery (gated — the controller runs this) ───────────────────────


@pytest.mark.skipif(
    not _LIVE,
    reason="PLAN-QUALITY live battery — set PLAN_QUALITY_LIVE=1 (real engine turns "
    "on the subscription + subscription-judged scoring; many model calls)",
)
def test_plan_quality_live() -> None:
    """Sampled pool scenarios on the REAL engine: traced, bounded, judged."""
    os.environ.pop("ANTHROPIC_API_KEY", None)  # subscription CLI auth only

    pool = load_pool()
    sample_n = int(os.environ["SAMPLE"]) if os.environ.get("SAMPLE") else None
    classes_env = os.environ.get("PLAN_QUALITY_CLASSES", "")
    classes = [c.strip() for c in classes_env.split(",") if c.strip()] or None
    sample = select_sample(pool, sample_n, classes=classes)
    assert sample, "empty sample — check SAMPLE / PLAN_QUALITY_CLASSES"

    async def _run_all() -> list[PlanAskResult]:
        results: list[PlanAskResult] = []
        for i, scenario in enumerate(sample):
            print(f"[{i + 1}/{len(sample)}] {scenario.id} ({scenario.ask_class})", flush=True)
            outcome = await run_plan_scenario(scenario, live_e2b=_LIVE_E2B)
            for r in outcome:
                m = r.metrics
                marks = (
                    f"ack={m.ack_latency_s:.1f}s done={m.complete_latency_s:.1f}s "
                    f"tools={m.tool_count} redundant={m.redundant_calls}"
                    if m is not None and m.ack_latency_s is not None
                    else "no-trace"
                )
                print(f"    {r.ask_id}: woke={r.woke} {marks}", flush=True)
            results.extend(outcome)
        return results

    results = asyncio.run(_run_all())

    # Judge + report FIRST — the full evidence always prints before any assert.
    scored = score_results(results)
    print(render_report(scored))
    artifact_dir = Path(
        os.environ.get("PLAN_QUALITY_ARTIFACT_DIR", tempfile.gettempdir())
    )
    note = f"n={len(sample)} classes={classes or 'all'} e2b={_LIVE_E2B}"
    path = write_artifact(
        build_artifact(scored, threshold=0.7, model=MODEL, sample_note=note), artifact_dir
    )
    print(f"[artifact] {path}", flush=True)

    # ── Deterministic invariants: hard facts, independent of any judge ────────
    for r in results:
        assert r.woke, f"{r.ask_id}: the addressed ask did not wake the engine"
        assert r.unexpected_wakes == 0, f"{r.ask_id}: context chatter woke the engine"
        for verb in r.require_transport:
            assert verb in r.transport_verbs, (
                f"{r.ask_id}: transport never recorded {verb!r} (saw {r.transport_verbs})"
            )
        if r.follow_up_woke is not None:
            assert r.follow_up_woke, f"{r.ask_id}: the un-prefixed clarify reply did not wake"

    # ── The deterministic per-class latency/plan bounds ───────────────────────
    violations = [(r.ask_id, v) for r in results for v in r.bound_violations]
    assert not violations, "deterministic bound violations:\n" + "\n".join(
        f"  {ask_id}: {v}" for ask_id, v in violations
    )

    # ── The judged plan-quality floor ─────────────────────────────────────────
    overall = statistics.fmean([s.score for s in scored])
    assert overall >= 0.70, f"plan-quality judge mean {overall:.3f} < 0.70"
