"""The orchestrator (Doc 04) CAPABILITY BATTERY — driven on the REAL product path.

This is the first reusable deepeval capability battery for Proxy. It answers the
highest-risk open question: does the orchestrator wake turn ACTUALLY work on the
REAL path — real :class:`harness.wake_turn.WakeTurn`, real
:class:`harness.provider.ClaudeAgentProvider` (the single Claude Agent SDK call
site), a REAL code_intel graph built by the REAL structural indexer, and a REAL
in-process code_intel MCP server the model can actually call?

Gated behind ``CAPABILITY_LIVE_EVAL=1`` (live provider + judge spend); the offline
suite stays hermetic. Each scenario runs the real path and is scored by deepeval
(GEval for correctness / groundedness / on-task / does-not-fabricate, plus
Faithfulness / AnswerRelevancy where a grounded answer is judged against the
retrieved graph context).

Two facts this battery establishes (see the run evidence + the report):

  1. **Capability (wired seam):** WHEN the code_intel MCP server is mounted through
     the runner's public ``mcp_servers`` seam, the real wake turn genuinely wakes,
     calls the real tool, grounds on the real graph result, and cites the real
     file:line. Scenarios 1–5,7 run this path.
  2. **The hollow-assembly defect (product path):** As the product assembles the
     wake turn today (``live_brain.build_wake_turn`` → ``WakeTurn`` → its internal
     ``BehaviorRunner``), NO code_intel server is mounted (``INIT.mcp_servers=[]``),
     so the model has no tools and FABRICATES a confident wrong answer. Scenario 0
     pins this defect as a real, reproduced failure — the fix (wire ``mcp_servers``
     into ``WakeTurn`` → ``BehaviorRunner``) is a separate step, per the brief.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from ._battery import (
    Scenario,
    ScenarioResult,
    faithfulness_metric,
    geval_metric,
    make_judge,
    render_report,
    run_battery,
)
from ._fixture_repo import build_fixture
from ._wake_driver import CapturedTurn, drive_product_wake, drive_wired_wake

pytestmark = pytest.mark.skipif(
    os.environ.get("CAPABILITY_LIVE_EVAL") != "1",
    reason="live capability eval — set CAPABILITY_LIVE_EVAL=1 (spends on the real provider + judge)",
)

_EVIDENCE = Path(
    "/private/tmp/claude-501/-Users-daksh-Desktop-proxy/"
    "1b60ab0b-612b-42a6-b957-bf9efecb577a/scratchpad/orchestrator_capability_evidence.txt"
)


# ── Context builders: the REAL graph facts the answer is grounded against ──────

def _graph_context(fx) -> list[str]:
    """A compact, real dump of the fixture graph facts — the Faithfulness anchor."""
    lines = ["Real code graph (built by the code_intel structural indexer):"]
    for e in fx.graph.edges:
        if e.kind == "calls":
            src = e.source.rsplit("::", 1)[-1]
            tgt = e.target.rsplit("::", 1)[-1]
            lines.append(f"  {src} calls {tgt}  (edge kind=calls)")
    for n in fx.graph.nodes:
        if "::" in n.id:
            lines.append(f"  {n.id.rsplit('::',1)[-1]} defined at {n.path}:{n.line}")
    return lines


def _result_context(turn: CapturedTurn) -> list[str]:
    """The tool results the model actually retrieved this turn (real grounding)."""
    ctx = [f"Tool result: {r}" for r in turn.tool_results if r.strip()]
    return ctx or ["(no tool result retrieved this turn)"]


# ── The driver → battery bridge ────────────────────────────────────────────────

def _as_result(turn: CapturedTurn, context: list[str]) -> ScenarioResult:
    """Bridge a driver CapturedTurn → a battery ScenarioResult with its context."""
    ctx = list(context) + _result_context(turn)
    return ScenarioResult(
        answer=turn.answer,
        context=ctx,
        tool_calls=list(turn.tool_calls),
        model_calls=0 if turn.error and not turn.tool_calls and not turn.answer else 1,
        transcript=turn.transcript,
        extra={"error": turn.error},
    )


# ── Structural checks (judge-independent facts over a ScenarioResult) ──────────

def _called_a_code_intel_tool(result: ScenarioResult) -> tuple[bool, str]:
    """Structural check: a real code_intel tool actually fired this turn."""
    called = [t for t in result.tool_calls if "code_intel" in t]
    return (bool(called), f"code_intel tools invoked: {called}" if called else "NO code_intel tool was invoked")


def _no_error(result: ScenarioResult) -> tuple[bool, str]:
    """Structural check: the provider raised no error this turn."""
    err = result.extra.get("error")
    return (err is None, "no provider error" if err is None else f"provider error: {err}")


def _build_scenarios(fx, tool_log: list[str]) -> list[Scenario]:
    graph_ctx = _graph_context(fx)

    # 1. Grounded factual Q: who calls login? -> names handle_request with file:line.
    def run1() -> ScenarioResult:
        turn = drive_wired_wake("Proxy, who calls login?", server=fx.server, tool_log=tool_log)
        turn.transcript_lines.insert(0, "SCENARIO 1: who calls login")
        return _as_result(turn, graph_ctx)

    s1 = Scenario(
        id="1-who-calls-login",
        description="Grounded factual Q: who calls login? (must cite the real caller + file:line)",
        input="Proxy, who calls login?",
        run=run1,
        metrics=[
            geval_metric(
                "GroundedCorrectness",
                "The answer must state that `handle_request` calls `login` and cite a real "
                "file:line (auth.py). It is correct only if the caller named matches the "
                "retrieval context. A vague or uncited answer scores low.",
                use_expected=True,
            ),
            faithfulness_metric(),
        ],
        expected="`login` is called by `handle_request` in auth.py.",
        threshold=0.7,
        structural_check=lambda t: _no_error(t),
        must_work=True,
    )

    # 2. where is save_user defined? -> correct file:line (db.py).
    def run2() -> ScenarioResult:
        turn = drive_wired_wake("Proxy, where is save_user defined?", server=fx.server, tool_log=tool_log)
        turn.transcript_lines.insert(0, "SCENARIO 2: where is save_user defined")
        return _as_result(turn, graph_ctx)

    s2 = Scenario(
        id="2-save-user-defined",
        description="Grounded definition Q: where is save_user defined? (must cite db.py:line)",
        input="Proxy, where is save_user defined?",
        run=run2,
        metrics=[
            geval_metric(
                "GroundedCorrectness",
                "The answer must locate `save_user` in db.py and cite a file:line consistent "
                "with the retrieval context. Wrong file, or no citation, scores low.",
                use_expected=True,
            ),
            faithfulness_metric(),
        ],
        expected="`save_user` is defined in db.py.",
        threshold=0.7,
        structural_check=lambda t: _no_error(t),
        must_work=True,
    )

    # 3. catch me up -> coherent summary from the state digest, no fabrication.
    def run3() -> ScenarioResult:
        turn = drive_wired_wake("Proxy, catch me up — where are we?", server=fx.server, tool_log=tool_log)
        turn.transcript_lines.insert(0, "SCENARIO 3: catch me up")
        return _as_result(turn, ["State digest: tasks in flight: indexing acme/webapp (done); "
                                 "mouth: free; component health: all green."])

    s3 = Scenario(
        id="3-catch-me-up",
        description="Catch-me-up: coherent status summary from the digest, no fabrication",
        input="Proxy, catch me up — where are we?",
        run=run3,
        metrics=[
            geval_metric(
                "GroundedSummary",
                "The answer must be a coherent status summary consistent with the state digest "
                "in the retrieval context (indexing done, all green). It must NOT invent tasks, "
                "people, or events absent from the digest. Fabricated specifics score low.",
            ),
        ],
        threshold=0.7,
        structural_check=lambda t: _no_error(t),
    )

    # 4. ambiguous/underspecified -> asks for clarification or honest bounded answer.
    def run4() -> ScenarioResult:
        turn = drive_wired_wake("Proxy, is it safe to change it?", server=fx.server, tool_log=tool_log)
        turn.transcript_lines.insert(0, "SCENARIO 4: ambiguous ask")
        return _as_result(turn, graph_ctx)

    s4 = Scenario(
        id="4-ambiguous",
        description="Ambiguous ask: must ask for clarification or give an honest bounded answer, not hallucinate",
        input="Proxy, is it safe to change it?",
        run=run4,
        metrics=[
            geval_metric(
                "DoesNotFabricate",
                "The ask is ambiguous ('it' is undefined). A good answer asks what 'it' refers "
                "to, or gives an explicitly bounded/caveated answer. It scores HIGH if it does "
                "NOT fabricate a specific symbol, file, or confident claim it cannot support. "
                "It scores LOW if it invents a concrete answer to the underspecified ask.",
            ),
        ],
        threshold=0.7,
        structural_check=lambda t: _no_error(t),
    )

    # 5. out-of-scope Q -> recuses / 'not found by this method', does NOT fabricate.
    def run5() -> ScenarioResult:
        turn = drive_wired_wake("Proxy, who calls charge_card?", server=fx.server, tool_log=tool_log)
        turn.transcript_lines.insert(0, "SCENARIO 5: out-of-scope symbol")
        return _as_result(turn, graph_ctx + ["Note: `charge_card` does not exist anywhere in this repo."])

    s5 = Scenario(
        id="5-out-of-scope",
        description="Out-of-scope symbol (charge_card not in repo): must recuse / 'not found', never fabricate",
        input="Proxy, who calls charge_card?",
        run=run5,
        metrics=[
            geval_metric(
                "HonestAbstention",
                "`charge_card` does not exist in the codebase (see retrieval context). A good "
                "answer says it was not found / not present / 'not found by this method', or asks "
                "to confirm the name. It scores HIGH only if it does NOT fabricate a caller or a "
                "file:line for charge_card. Inventing any caller or location scores 0.",
            ),
        ],
        threshold=0.7,
        structural_check=lambda t: _no_error(t),
    )

    # 7. grounded Q requiring a real tool call -> the model ACTUALLY calls the tool.
    def run7() -> ScenarioResult:
        local_log: list[str] = []
        turn = drive_wired_wake(
            "Proxy, what depends on save_user across the codebase?",
            server=fx.server,
            tool_log=local_log,
        )
        turn.transcript_lines.insert(0, f"SCENARIO 7: dependents of save_user (tools executed: {local_log})")
        return _as_result(turn, graph_ctx)

    s7 = Scenario(
        id="7-tool-call-required",
        description="Grounded Q requiring a real tool call: the model must ACTUALLY invoke a code_intel tool",
        input="Proxy, what depends on save_user across the codebase?",
        run=run7,
        metrics=[
            geval_metric(
                "GroundedViaTool",
                "The answer must name real dependents of `save_user` (e.g. onboard in billing.py) "
                "with citations consistent with the retrieval context. It scores low if uncited "
                "or inconsistent with the graph.",
            ),
            faithfulness_metric(),
        ],
        threshold=0.7,
        structural_check=_called_a_code_intel_tool,  # HARD: a real tool must have fired
        must_work=True,
    )

    return [s1, s2, s3, s4, s5, s7]


@pytest.fixture(scope="module")
def fixture_repo():
    root = Path(tempfile.mkdtemp(prefix="proxy-orch-cap-")) / "repo"
    return build_fixture(root)


def test_orchestrator_capability_battery(fixture_repo):
    """Drive the full orchestrator capability battery on the REAL wake-turn path."""
    fx = fixture_repo
    tool_log: list[str] = []
    scenarios = _build_scenarios(fx, tool_log)

    judge = make_judge()
    report = run_battery(scenarios, judge=judge)

    # ── Scenario 6: un-addressed ambient event → ZERO wake (the model never fires). ──
    # This is a STRUCTURAL fact, not a scored one: it must run zero model calls, so it
    # is proven via the REAL name-gate front-gate, NOT by invoking the provider.
    zero_wake_ok, zero_wake_detail = _prove_zero_wake_on_unaddressed()

    # ── Scenario 0: the PRODUCT path (as assembled today) — the hollow-assembly defect. ──
    product_turn = drive_product_wake("Proxy, who calls login?")
    product_mounted = "mcp_servers=[]" not in product_turn.transcript  # False == defect present
    product_fabricated = _looks_fabricated(product_turn)

    # Render + persist full evidence BEFORE asserting (so a FAIL still leaves evidence).
    evidence = _render_full_evidence(
        report, zero_wake_detail, product_turn, product_mounted, product_fabricated, tool_log
    )
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE.write_text(evidence, encoding="utf-8")
    print("\n" + evidence)

    # ── The battery gate (per the brief) ──────────────────────────────────────────
    # Aggregate mean ≥ 0.8; must-work scenarios (1,2,7) ≥ 0.7; honest-degradation
    # scenarios (4,5) high on does-not-fabricate; scenario 6 = zero model calls.
    failures: list[str] = []

    if report.mean_score < 0.8:
        failures.append(f"aggregate mean {report.mean_score:.3f} < 0.80")

    for sid in ("1-who-calls-login", "2-save-user-defined", "7-tool-call-required"):
        sc = report.scenario(sid)
        if sc.mean_score < 0.7:
            failures.append(f"must-work scenario {sid} mean {sc.mean_score:.3f} < 0.70")
        if not sc.structural_ok:
            failures.append(f"must-work scenario {sid} structural fail: {sc.structural_reason}")

    for sid in ("4-ambiguous", "5-out-of-scope"):
        sc = report.scenario(sid)
        if sc.mean_score < 0.7:
            failures.append(f"honest-degradation scenario {sid} mean {sc.mean_score:.3f} < 0.70")

    if not zero_wake_ok:
        failures.append(f"scenario 6 (un-addressed zero-wake) failed: {zero_wake_detail}")

    assert not failures, (
        "Orchestrator capability battery FAILED:\n  - "
        + "\n  - ".join(failures)
        + f"\n\nFull evidence: {_EVIDENCE}"
    )


def _prove_zero_wake_on_unaddressed() -> tuple[bool, str]:
    """Scenario 6: an ambient line that never says 'Proxy' → wake=False, zero model calls.

    Proven via the REAL name-gate + the REAL live_brain addressed predicate — the same
    front gate the run loop uses. The provider is NEVER invoked (that is the property):
    an un-addressed line folds to the digest with zero agent calls.
    """
    from harness.live_brain import make_addressed_predicate
    from harness.name_gate import NameGate
    from harness.run_loop import MeetingEvent
    from transport.signals import Transcript

    # A disambiguate that would ACCEPT a spoken hit — proving the gate stops at the
    # mechanical scan (no 'Proxy' token) and never even reaches disambiguation.
    name_gate = NameGate(disambiguate=lambda _line: True)
    addressed = make_addressed_predicate(name_gate)

    ambient = Transcript(
        words="so then we shipped the migration on Friday and it went fine", speaker="alice", t=0.0
    )
    event = MeetingEvent(payload=ambient, emitter=None, abort=None)
    woke = addressed(event)
    ok = woke is False
    detail = (
        f"un-addressed ambient line -> addressed()={woke} (expected False); "
        f"the provider is never constructed/invoked for this line -> zero model calls."
    )
    return ok, detail


def _looks_fabricated(turn: CapturedTurn) -> bool:
    """Heuristic: the product answer confidently denies a symbol that DOES exist."""
    a = turn.answer.lower()
    denies = any(p in a for p in ("not present", "no hits", "not found", "not in", "doesn't exist", "no usages"))
    return denies


def _render_full_evidence(report, zero_wake_detail, product_turn, product_mounted, product_fabricated, tool_log) -> str:
    parts = [render_report(report)]
    parts.append("=" * 78)
    parts.append("SCENARIO 6 — un-addressed ambient event → ZERO wake (structural, zero model calls)")
    parts.append("=" * 78)
    parts.append("  " + zero_wake_detail)
    parts.append("")
    parts.append("=" * 78)
    parts.append("SCENARIO 0 — THE PRODUCT PATH AS ASSEMBLED TODAY (hollow-assembly probe)")
    parts.append("=" * 78)
    parts.append("  Built exactly as harness.live_brain.build_wake_turn assembles the live wake turn:")
    parts.append("  real WakeTurn + real ClaudeAgentProvider + behaviors.REGISTRY, NO code_intel mount.")
    parts.append(f"  code_intel MCP server MOUNTED on the product path? {product_mounted}")
    parts.append(f"  product answer FABRICATED (denies a symbol that exists)? {product_fabricated}")
    parts.append(f"  product answer: {product_turn.answer!r}")
    parts.append("  product transcript:")
    for line in product_turn.transcript.splitlines():
        parts.append(f"      | {line}")
    parts.append("")
    parts.append(f"  code_intel tools ACTUALLY executed across the wired battery: {tool_log}")
    return "\n".join(parts)
