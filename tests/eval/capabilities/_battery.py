"""A small, reusable deepeval **capability battery** runner for Proxy.

A capability battery is a list of :class:`Scenario` — each pairs a REAL product
callable (the thing that runs the real path and returns an answer + the retrieved
context it grounded on) with a set of deepeval metrics and a pass threshold. The
runner:

  1. runs each scenario's real callable (the REAL product path — no mock),
  2. builds a deepeval ``LLMTestCase`` from the produced answer + retrieved
     context,
  3. scores it with the scenario's metrics (GEval for correctness / groundedness /
     on-task / does-not-fabricate, plus Faithfulness / AnswerRelevancy where a
     grounded answer is judged against retrieved context),
  4. returns a per-scenario + aggregate :class:`BatteryReport` (mean, min, per-
     scenario pass/fail vs threshold).

It is deliberately generic: the orchestrator battery is the first user, but any
Proxy component can hand the runner a callable that runs its real path and a set
of metrics. Live scoring (which calls a judge model) is gated by the caller —
this module never decides to spend; it just runs what it is given.

The judge model defaults to the pinned Sonnet seat (``claude-sonnet-4-6``, the
same seat ``tests/eval/deepeval_config.JUDGE_MODEL`` names) via deepeval's
``AnthropicModel`` — keyed off ``ANTHROPIC_API_KEY``.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

# deepeval is imported lazily inside the runner so this module is importable in the
# offline suite (it only *runs* under CAPABILITY_LIVE_EVAL=1).


@dataclass
class ScenarioResult:
    """One scenario's real product output (what the callable returns)."""

    #: The answer text the real product path produced (spoken/typed to the user).
    answer: str
    #: The retrieved context the answer is grounded on (e.g. the real graph tool
    #: results / the state digest) — the deepeval Faithfulness/relevancy anchor.
    context: list[str] = field(default_factory=list)
    #: The tool names the model actually invoked this turn (for tool-call asserts).
    tool_calls: list[str] = field(default_factory=list)
    #: How many times the model was actually invoked (0 = the model never woke).
    model_calls: int = 1
    #: Free-form transcript / evidence captured for the report.
    transcript: str = ""
    #: Any extra structured facts a scenario wants recorded.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricSpec:
    """A deepeval metric to apply to a scenario, resolved lazily at run time.

    ``builder`` returns a fresh deepeval metric instance (so each scenario gets its
    own, thread-safe, judge-bound metric). ``kind`` records what the metric needs
    from the test case so the runner can supply the right params.
    """

    name: str
    builder: Callable[[Any], Any]  # (judge_model) -> deepeval metric instance
    #: "geval" (input/actual/expected), "faithfulness" (actual + retrieval_context),
    #: or "relevancy" (input + actual).
    kind: str = "geval"


@dataclass
class Scenario:
    """One capability-battery scenario driving the REAL product path."""

    id: str
    description: str
    #: The verbatim ask / input handed to the product path.
    input: str
    #: The REAL callable that runs the product path and returns a ScenarioResult.
    run: Callable[[], ScenarioResult]
    #: The deepeval metrics to score the produced answer with.
    metrics: Sequence[MetricSpec]
    #: The pass threshold for this scenario's mean metric score.
    threshold: float = 0.7
    #: An optional expected-answer anchor for GEval correctness.
    expected: str | None = None
    #: A hard structural assertion over the ScenarioResult (e.g. "a tool was
    #: called", "the model never woke"). Returns (ok, reason). Independent of the
    #: judge — a structural fact, not a scored one.
    structural_check: Callable[[ScenarioResult], tuple[bool, str]] | None = None
    #: Whether this scenario is a "must-work" one (feeds the strict aggregate gate).
    must_work: bool = False


@dataclass
class MetricScore:
    name: str
    score: float
    reason: str
    passed: bool


@dataclass
class ScenarioReport:
    id: str
    description: str
    input: str
    answer: str
    context: list[str]
    tool_calls: list[str]
    model_calls: int
    transcript: str
    metric_scores: list[MetricScore]
    mean_score: float
    threshold: float
    structural_ok: bool
    structural_reason: str
    passed: bool
    must_work: bool
    error: str | None = None


@dataclass
class BatteryReport:
    scenarios: list[ScenarioReport]

    @property
    def mean_score(self) -> float:
        vals = [s.mean_score for s in self.scenarios if s.error is None]
        return statistics.fmean(vals) if vals else 0.0

    @property
    def min_score(self) -> float:
        vals = [s.mean_score for s in self.scenarios if s.error is None]
        return min(vals) if vals else 0.0

    @property
    def passed_count(self) -> int:
        return sum(1 for s in self.scenarios if s.passed)

    @property
    def total(self) -> int:
        return len(self.scenarios)

    def scenario(self, scenario_id: str) -> ScenarioReport:
        for s in self.scenarios:
            if s.id == scenario_id:
                return s
        raise KeyError(scenario_id)


def make_judge(model: str | None = None) -> Any:
    """Build the deepeval judge (defaults to the pinned Sonnet seat, Anthropic)."""
    from deepeval.models import AnthropicModel

    from tests.eval.deepeval_config import JUDGE_MODEL

    return AnthropicModel(model=model or JUDGE_MODEL)


# ── Metric builders (the common, reusable capability metrics) ─────────────────

def geval_metric(
    name: str,
    criteria: str,
    *,
    threshold: float = 0.7,
    use_expected: bool = False,
) -> MetricSpec:
    """A GEval metric scoring the actual answer against ``criteria``.

    When ``use_expected`` is True the metric also reads the expected-output param
    (correctness-style); otherwise it judges the actual output against the input
    alone (on-task / does-not-fabricate style).
    """

    def _build(judge: Any) -> Any:
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCaseParams

        params = [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT]
        if use_expected:
            params.append(LLMTestCaseParams.EXPECTED_OUTPUT)
        params.append(LLMTestCaseParams.RETRIEVAL_CONTEXT)
        return GEval(name=name, criteria=criteria, evaluation_params=params, model=judge, threshold=threshold)

    return MetricSpec(name=name, builder=_build, kind="geval")


def faithfulness_metric(threshold: float = 0.7) -> MetricSpec:
    def _build(judge: Any) -> Any:
        from deepeval.metrics import FaithfulnessMetric

        return FaithfulnessMetric(model=judge, threshold=threshold)

    return MetricSpec(name="Faithfulness", builder=_build, kind="faithfulness")


def answer_relevancy_metric(threshold: float = 0.7) -> MetricSpec:
    def _build(judge: Any) -> Any:
        from deepeval.metrics import AnswerRelevancyMetric

        return AnswerRelevancyMetric(model=judge, threshold=threshold)

    return MetricSpec(name="AnswerRelevancy", builder=_build, kind="relevancy")


# ── The runner ────────────────────────────────────────────────────────────────

def _score_metric(spec: MetricSpec, judge: Any, scenario: Scenario, result: ScenarioResult) -> MetricScore:
    from deepeval.test_case import LLMTestCase

    metric = spec.builder(judge)
    retrieval_context = list(result.context) if result.context else None
    test_case = LLMTestCase(
        input=scenario.input,
        actual_output=result.answer or "(no answer produced)",
        expected_output=scenario.expected,
        # deepeval accepts a plain list[str] at runtime; its stub types this field as a
        # str|RetrievedContextData union whose list is invariant, so a bare list[str] trips
        # the checker. The value is correct; the ignore is scoped to that stub quirk only.
        retrieval_context=retrieval_context,  # type: ignore[arg-type]
        context=retrieval_context,  # type: ignore[arg-type]
    )
    metric.measure(test_case)
    score = float(metric.score if metric.score is not None else 0.0)
    reason = str(getattr(metric, "reason", "") or "")
    threshold = float(getattr(metric, "threshold", scenario.threshold))
    return MetricScore(name=spec.name, score=score, reason=reason, passed=score >= threshold)


def run_scenario(scenario: Scenario, judge: Any) -> ScenarioReport:
    """Run ONE scenario's real path and score it. Never raises — a failure is
    captured as an ``error`` on the report so the battery always completes."""
    try:
        result = scenario.run()
    except Exception as exc:  # noqa: BLE001 - a scenario blow-up is a captured FAIL, not a crash
        return ScenarioReport(
            id=scenario.id,
            description=scenario.description,
            input=scenario.input,
            answer="",
            context=[],
            tool_calls=[],
            model_calls=0,
            transcript=f"SCENARIO RAISED: {type(exc).__name__}: {exc}",
            metric_scores=[],
            mean_score=0.0,
            threshold=scenario.threshold,
            structural_ok=False,
            structural_reason=f"scenario raised: {exc}",
            passed=False,
            must_work=scenario.must_work,
            error=f"{type(exc).__name__}: {exc}",
        )

    structural_ok, structural_reason = (True, "")
    if scenario.structural_check is not None:
        structural_ok, structural_reason = scenario.structural_check(result)

    metric_scores: list[MetricScore] = []
    for spec in scenario.metrics:
        try:
            metric_scores.append(_score_metric(spec, judge, scenario, result))
        except Exception as exc:  # noqa: BLE001 - a judge failure is a captured 0, not a crash
            metric_scores.append(
                MetricScore(name=spec.name, score=0.0, reason=f"metric error: {exc}", passed=False)
            )

    mean_score = statistics.fmean([m.score for m in metric_scores]) if metric_scores else 0.0
    metrics_pass = mean_score >= scenario.threshold if metric_scores else True
    passed = structural_ok and metrics_pass

    return ScenarioReport(
        id=scenario.id,
        description=scenario.description,
        input=scenario.input,
        answer=result.answer,
        context=result.context,
        tool_calls=result.tool_calls,
        model_calls=result.model_calls,
        transcript=result.transcript,
        metric_scores=metric_scores,
        mean_score=mean_score,
        threshold=scenario.threshold,
        structural_ok=structural_ok,
        structural_reason=structural_reason,
        passed=passed,
        must_work=scenario.must_work,
    )


def run_battery(scenarios: Sequence[Scenario], *, judge: Any | None = None) -> BatteryReport:
    """Run every scenario's real path and score it — the whole battery."""
    judge = judge if judge is not None else make_judge()
    reports = [run_scenario(s, judge) for s in scenarios]
    return BatteryReport(scenarios=reports)


# ── Report rendering (for the evidence file + assertion messages) ─────────────

def render_report(report: BatteryReport) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("CAPABILITY BATTERY REPORT")
    lines.append("=" * 78)
    lines.append(
        f"aggregate mean = {report.mean_score:.3f}   min = {report.min_score:.3f}   "
        f"passed {report.passed_count}/{report.total}"
    )
    lines.append("")
    for s in report.scenarios:
        verdict = "PASS" if s.passed else "FAIL"
        star = " [MUST-WORK]" if s.must_work else ""
        lines.append("-" * 78)
        lines.append(f"[{verdict}]{star} {s.id} — {s.description}")
        lines.append(f"  input:       {s.input}")
        lines.append(f"  model_calls: {s.model_calls}")
        lines.append(f"  tool_calls:  {s.tool_calls}")
        lines.append(f"  answer:      {s.answer!r}")
        if s.error:
            lines.append(f"  ERROR:       {s.error}")
        lines.append(f"  structural:  ok={s.structural_ok} — {s.structural_reason}")
        for m in s.metric_scores:
            mv = "pass" if m.passed else "fail"
            lines.append(f"  metric {m.name:<18} score={m.score:.3f} [{mv}]")
            if m.reason:
                lines.append(f"      reason: {m.reason}")
        lines.append(f"  mean_score:  {s.mean_score:.3f} (threshold {s.threshold})")
        if s.context:
            lines.append("  retrieved context:")
            for c in s.context:
                for cl in str(c).splitlines():
                    lines.append(f"      | {cl}")
        if s.transcript:
            lines.append("  transcript:")
            for tl in s.transcript.splitlines():
                lines.append(f"      | {tl}")
        lines.append("")
    return "\n".join(lines)
