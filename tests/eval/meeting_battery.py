"""A-FINAL long-meeting battery runner — the REAL engine, driven line-by-line.

Builds ONE real ``in_meeting.engine.Engine`` per scenario and feeds the committed
long-meeting script (``tests/eval/scenarios_long_meetings.py``) through it exactly
as a live meeting would arrive:

* **Grounded code tools are REAL**: ``premeeting.RepoContext(clone_path=<the
  committed battery_repo fixture>, map_text=BATTERY_REPO_MAP).build_server()``
  mounted as ``code_intel`` with the engine's ``CODE_TOOLS`` — ripgrep + bounded
  reads over a real clone on disk.
* **Meeting control is REAL tooling over a FAKE transport**: the product's
  ``build_meeting_control_server`` mounted over a recording
  :class:`FakeMeetingTransport` — the agent's mute/post_chat tool calls execute
  the real MCP handler path and land as recorded verbs (deterministic evidence).
* **Sandbox — two documented modes**: with ``RUN_BATTERY_LIVE_E2B=1`` the runner
  provisions a REAL E2B sandbox (``in_meeting.sandbox.provision_sandbox``) and
  mounts ``build_sandbox_server`` over it, so "run it and tell me" asks execute
  for real. Without it, NO sandbox tools are mounted and the sandbox-exec asks
  are judged against their ``expect_no_sandbox`` bar ("acknowledges it can't run
  code right now") — a canned fake sandbox returning invented CommandResults
  would be DISHONEST evidence for an exec ask, so it does not exist here.
* **The provider is the REAL ``EngineProvider``** in live mode (the Claude Max
  subscription — ``ANTHROPIC_API_KEY`` is popped, CLI auth only), wrapped in an
  :class:`ObservingProvider` that tees every chunk into a per-turn record
  (prompt, tool calls, accumulated text, errors) — turn→ask attribution keys on
  the volatile "You were addressed:" suffix, so concurrent turns attribute
  exactly. The offline rot-proof passes a scripted fake provider instead.
* **Disambiguation is DETERMINISTIC by design**: an addressed line starts with
  the wake name ("Proxy, ..."); common-noun chatter never does. Scoring the
  model disambiguator is that seam's own eval — pinning it here makes the idle
  zero-wake assertions hard facts instead of judged ones.

The runner drains after each ask (deterministic scoring points), records honest
per-ask outcomes (it asserts nothing itself — the test asserts), and
:func:`score_runs` scores every ask with deepeval G-Eval on the subscription
judge (``tests/eval/subscription_judge.py``, ~$0), aggregating per-stressor and
overall means. :func:`render_report` emits the compact evidence report.
"""
from __future__ import annotations

import contextlib
import os
import re
import statistics
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.eval.scenarios_long_meetings import (
    BATTERY_REPO_MAP,
    Ask,
    Concurrent,
    Idle,
    Line,
    MeetingScenario,
    Say,
)

#: The pinned engine seat for the live battery (the Sonnet seat, as the plan names).
MODEL = "claude-sonnet-4-6"

#: The per-meeting bot identity the meeting-control server is bound to.
BOT_ID = "proxy-battery-bot"

#: One wake turn's SDK-loop budget (the engine's proven multi-turn default).
MAX_TURNS = 16

#: An addressed line STARTS with the wake name — the deterministic convention the
#: committed scenarios are authored against (see the module docstring).
_ADDRESSED_RE = re.compile(r"^\s*proxy\b", re.IGNORECASE)

#: The honest telemetry label appended to the judged output (real observed tool
#: names from the wrapper — never model-claimed).
_TELEMETRY_LABEL = "[battery telemetry — tools actually invoked for this ask:"


def battery_repo_path() -> Path:
    """The committed battery clone fixture (tests/fixtures/battery_repo)."""
    return Path(__file__).resolve().parents[1] / "fixtures" / "battery_repo"


async def deterministic_disambiguate(text: str) -> bool:
    """The injected trigger judgment seam, pinned deterministic for the battery."""
    return _ADDRESSED_RE.match(text) is not None


# ── The fake meeting transport (recording; the product handler path is real) ──


class FakeMeetingTransport:
    """Records every meeting-control verb the real MCP handlers drive."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def mute(self, bot_id: str) -> None:
        self.calls.append(("mute", {"bot_id": bot_id}))

    async def unmute(self, bot_id: str) -> None:
        self.calls.append(("unmute", {"bot_id": bot_id}))

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.calls.append(("post_chat", {"bot_id": bot_id, "message": message, "pinned": pinned}))

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.calls.append(
            ("send_dm", {"bot_id": bot_id, "message": message, "participant_id": participant_id})
        )


# ── The observing provider wrapper (turn→ask attribution + tool telemetry) ────


@dataclass
class TurnRecord:
    """Everything one provider turn actually did, teed off the chunk stream."""

    prompt: str
    tool_calls: list[str] = field(default_factory=list)
    tool_inputs: list[dict[str, Any]] = field(default_factory=list)
    _texts: dict[str, str] = field(default_factory=dict)
    _msg_order: list[str] = field(default_factory=list)
    result_text: str = ""
    error: str | None = None

    @property
    def addressed(self) -> str:
        """The volatile ask suffix of the turn prompt — the exact attribution key
        (the recent-notes block above it may CONTAIN other asks' lines)."""
        return self.prompt.rsplit("You were addressed:", 1)[-1]

    @property
    def response_text(self) -> str:
        """The turn's spoken text (accumulated per msg_id), result text fallback."""
        spoken = "\n".join(self._texts[m] for m in self._msg_order if self._texts[m].strip())
        return spoken or self.result_text

    def note_text(self, msg_id: str, accumulated: str) -> None:
        if msg_id not in self._texts:
            self._msg_order.append(msg_id)
        self._texts[msg_id] = accumulated


class ObservingProvider:
    """A transparent tee around ANY ``agentkit.Provider``: chunks pass through
    unchanged; every turn lands in :attr:`records` (call order)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.records: list[TurnRecord] = []

    async def stream(self, prompt: str, query: Any) -> AsyncIterator[Any]:
        record = TurnRecord(prompt=prompt)
        self.records.append(record)
        try:
            async for chunk in self._inner.stream(prompt, query):
                ctype = getattr(chunk, "type", "")
                meta = getattr(chunk, "metadata", {}) or {}
                if ctype == "TEXT":
                    record.note_text(str(meta.get("msg_id", "")), getattr(chunk, "text", "") or "")
                elif ctype == "TOOL_USE":
                    record.tool_calls.append(str(meta.get("name", "")))
                    record.tool_inputs.append(dict(meta.get("input", {}) or {}))
                elif ctype == "RESULT":
                    record.result_text = getattr(chunk, "text", "") or ""
                elif ctype == "ERROR" and record.error is None:
                    record.error = str(meta.get("message", "")) or "provider error"
                yield chunk
        except Exception as exc:
            if record.error is None:
                record.error = f"{type(exc).__name__}: {exc}"
            raise


# ── Honest per-scenario outcomes (recorded, never asserted here) ──────────────


@dataclass
class AskOutcome:
    """One embedded ask's observed outcome on the real path."""

    ask_id: str
    stressor: str
    ask_text: str
    #: The judge criteria that APPLY for this run (the no-sandbox bar swaps in
    #: for sandbox-exec asks when no sandbox is mounted).
    criteria: str
    woke: bool
    response_text: str
    tool_calls: tuple[str, ...]
    transport_verbs: tuple[str, ...]
    require_transport: tuple[str, ...]
    turns_completed: int
    error: str | None
    #: clarify flow only: did the un-prefixed reply wake as source="reply"?
    follow_up_woke: bool | None = None
    #: Set to the Concurrent block id for back-to-back asks (else "").
    concurrent_group: str = ""


@dataclass
class IdleOutcome:
    """One idle/common-noun stretch's observed wake count (must be zero)."""

    stretch_id: str
    lines: int
    wakes: int


@dataclass
class ScenarioRun:
    """One scenario's full honest record."""

    scenario_id: str
    sandbox_mounted: bool
    asks: list[AskOutcome] = field(default_factory=list)
    idles: list[IdleOutcome] = field(default_factory=list)
    unexpected_wakes: int = 0
    transport_log: tuple[str, ...] = ()
    spoken_deltas: int = 0


def _criteria_for(ask: Ask, *, sandbox_mounted: bool) -> str:
    if ask.stressor == "sandbox-exec" and not sandbox_mounted and ask.expect_no_sandbox:
        return ask.expect_no_sandbox
    return ask.expect


def _records_for(observed: ObservingProvider, needle: str) -> list[TurnRecord]:
    """The provider turns whose addressed suffix carries ``needle`` (exact key)."""
    return [r for r in observed.records if needle in r.addressed]


def _combine_response(records: list[TurnRecord], labels: list[str]) -> str:
    if not records:
        return ""
    if len(records) == 1:
        return records[0].response_text
    parts = []
    for label, record in zip(labels, records):
        parts.append(f"[{label}]\n{record.response_text}")
    return "\n\n".join(parts)


# ── The runner ─────────────────────────────────────────────────────────────────


async def run_scenario(
    scenario: MeetingScenario,
    *,
    provider: Any | None = None,
    live_e2b: bool = False,
    model: str = MODEL,
) -> ScenarioRun:
    """Drive ONE committed scenario line-by-line through a real Engine.

    ``provider=None`` builds the REAL ``EngineProvider`` (live mode — the Claude
    Max subscription; the paid key is popped first). Tests pass a scripted fake
    for the offline rot-proof. ``live_e2b=True`` provisions a REAL E2B sandbox
    for the scenario (killed at the end); otherwise no sandbox tools are mounted
    (the honest no-exec mode — see the module docstring).
    """
    # Subscription CLI auth only — mirror the smokes; never the paid API.
    os.environ.pop("ANTHROPIC_API_KEY", None)

    from in_meeting.engine import CODE_TOOLS, Engine
    from in_meeting.meeting_control import MEETING_TOOLS, build_meeting_control_server
    from in_meeting.notes import TranscriptLine
    from premeeting.repo_context import RepoContext

    repo = battery_repo_path()
    code_server = RepoContext(clone_path=repo, map_text=BATTERY_REPO_MAP).build_server()
    if code_server is None:  # the committed fixture is the battery's substrate
        raise RuntimeError(f"battery_repo fixture unavailable at {repo} — no code server built")

    transport = FakeMeetingTransport()
    mcp_servers: dict[str, Any] = {
        "code_intel": code_server,
        "meeting": build_meeting_control_server(transport, bot_id=BOT_ID),
    }
    allowed_tools: tuple[str, ...] = tuple(CODE_TOOLS) + tuple(MEETING_TOOLS)

    sandbox_handle: Any | None = None
    if live_e2b:
        from in_meeting.sandbox import SANDBOX_TOOLS, build_sandbox_server, provision_sandbox

        sandbox_handle = await provision_sandbox()
        mcp_servers["sandbox"] = build_sandbox_server(sandbox_handle)
        allowed_tools = allowed_tools + tuple(SANDBOX_TOOLS)

    if provider is None:
        from in_meeting.provider import EngineProvider

        provider = EngineProvider()
    observed = ObservingProvider(provider)

    spoken: list[str] = []

    async def _speak(text: str) -> None:
        spoken.append(text)

    engine = Engine(
        model=model,
        allowed_tools=allowed_tools,
        speak=_speak,
        disambiguate=deterministic_disambiguate,
        provider=observed,
        map_text=BATTERY_REPO_MAP,
        mcp_servers=mcp_servers,
        max_turns=MAX_TURNS,
    )

    run = ScenarioRun(scenario_id=scenario.id, sandbox_mounted=live_e2b)
    clock = {"t": 0.0}

    def _tline(line: Line) -> Any:
        clock["t"] += 4.0
        return TranscriptLine(
            text=line.text, speaker=line.speaker, timestamp=clock["t"], end_of_turn=True
        )

    try:
        for event in scenario.events:
            if isinstance(event, Say):
                if await engine.feed_transcript(_tline(event.line)) is not None:
                    run.unexpected_wakes += 1
            elif isinstance(event, Idle):
                wakes = 0
                for line in event.lines:
                    if await engine.feed_transcript(_tline(line)) is not None:
                        wakes += 1
                run.idles.append(IdleOutcome(stretch_id=event.id, lines=len(event.lines), wakes=wakes))
            elif isinstance(event, Ask):
                run.asks.append(
                    await _drive_ask(engine, event, transport, observed, _tline, live_e2b)
                )
            elif isinstance(event, Concurrent):
                run.asks.extend(
                    await _drive_concurrent(engine, event, transport, observed, _tline, live_e2b)
                )
        await engine.drain()
    finally:
        if sandbox_handle is not None:
            with contextlib.suppress(Exception):
                await sandbox_handle.kill()

    run.transport_log = tuple(verb for verb, _ in transport.calls)
    run.spoken_deltas = len(spoken)
    return run


async def _drive_ask(
    engine: Any,
    ask: Ask,
    transport: FakeMeetingTransport,
    observed: ObservingProvider,
    tline: Callable[[Line], Any],
    sandbox_mounted: bool,
) -> AskOutcome:
    """One embedded ask: feed → drain (a deterministic scoring point); for the
    clarify flow, arm the follow-up window after Proxy's clarifying turn (the
    sims' pattern — arming is the caller's judgment seam) and feed the reply."""
    transport_before = len(transport.calls)
    turns_before = len(engine.turns)

    engagement = await engine.feed_transcript(tline(ask.line))
    woke = engagement is not None
    await engine.drain()

    follow_up_woke: bool | None = None
    if ask.follow_up is not None:
        engine.arm_pending_ask()
        reply = await engine.feed_transcript(tline(ask.follow_up))
        follow_up_woke = reply is not None and getattr(reply, "source", "") == "reply"
        await engine.drain()

    turns_completed = len(engine.turns) - turns_before
    records = _records_for(observed, ask.line.text)
    labels = ["clarifying turn"]
    if ask.follow_up is not None:
        records = records + _records_for(observed, ask.follow_up.text)
        labels.append("after the clarification")
    response = _combine_response(records, labels)
    tool_calls = tuple(name for r in records for name in r.tool_calls)
    error = next((r.error for r in records if r.error), None)
    return AskOutcome(
        ask_id=ask.id,
        stressor=ask.stressor,
        ask_text=ask.line.text,
        criteria=_criteria_for(ask, sandbox_mounted=sandbox_mounted),
        woke=woke,
        response_text=response,
        tool_calls=tool_calls,
        transport_verbs=tuple(verb for verb, _ in transport.calls[transport_before:]),
        require_transport=ask.require_transport,
        turns_completed=turns_completed,
        error=error,
        follow_up_woke=follow_up_woke,
    )


async def _drive_concurrent(
    engine: Any,
    block: Concurrent,
    transport: FakeMeetingTransport,
    observed: ObservingProvider,
    tline: Callable[[Line], Any],
    sandbox_mounted: bool,
) -> list[AskOutcome]:
    """Two addressed lines back-to-back, NO drain between — both turns must then
    complete via one drain; attribution keys on each turn's addressed suffix."""
    transport_before = len(transport.calls)
    turns_before = len(engine.turns)

    woke_first = await engine.feed_transcript(tline(block.first.line)) is not None
    woke_second = await engine.feed_transcript(tline(block.second.line)) is not None
    await engine.drain()

    turns_completed = len(engine.turns) - turns_before
    new_verbs = tuple(verb for verb, _ in transport.calls[transport_before:])

    outcomes: list[AskOutcome] = []
    for ask, woke in ((block.first, woke_first), (block.second, woke_second)):
        records = _records_for(observed, ask.line.text)
        outcomes.append(
            AskOutcome(
                ask_id=ask.id,
                stressor=ask.stressor,
                ask_text=ask.line.text,
                criteria=_criteria_for(ask, sandbox_mounted=sandbox_mounted),
                woke=woke,
                response_text=_combine_response(records, ["turn"]),
                tool_calls=tuple(name for r in records for name in r.tool_calls),
                transport_verbs=new_verbs,
                require_transport=ask.require_transport,
                turns_completed=turns_completed,
                error=next((r.error for r in records if r.error), None),
                concurrent_group=block.id,
            )
        )
    return outcomes


# ── Scoring (deepeval G-Eval on the subscription judge; lazy imports) ─────────

_JUDGE_PREAMBLE = (
    "You are judging ONE spoken response from Proxy, an AI participant in a live "
    "engineering meeting, against the behavioral criteria below. Judge BEHAVIOR, "
    "not phrasing — wording may vary freely. The actual output may end with a "
    "bracketed battery-telemetry line listing the tool names the system ACTUALLY "
    "observed Proxy invoke for this ask; that telemetry is ground truth (it is "
    "not model-claimed) — use it when the criteria concern whether something was "
    "really done. Criteria: "
)


@dataclass
class AskScore:
    outcome: AskOutcome
    score: float
    reason: str
    passed: bool


@dataclass
class BatteryScore:
    """Every ask judged + the deterministic run records, with the aggregates."""

    runs: list[ScenarioRun]
    ask_scores: list[AskScore]
    threshold: float

    @property
    def overall_mean(self) -> float:
        vals = [s.score for s in self.ask_scores]
        return statistics.fmean(vals) if vals else 0.0

    @property
    def per_stressor(self) -> dict[str, float]:
        buckets: dict[str, list[float]] = {}
        for s in self.ask_scores:
            buckets.setdefault(s.outcome.stressor, []).append(s.score)
        return {k: statistics.fmean(v) for k, v in sorted(buckets.items())}


def _judged_output(outcome: AskOutcome) -> str:
    text = outcome.response_text.strip() or "(no spoken response was captured for this ask)"
    tools = ", ".join(outcome.tool_calls) if outcome.tool_calls else "none"
    return f"{text}\n\n{_TELEMETRY_LABEL} {tools}]"


def score_runs(
    runs: list[ScenarioRun], *, judge: Any | None = None, threshold: float = 0.75
) -> BatteryScore:
    """Score every recorded ask with GEval on the subscription judge (~$0).

    A judge/metric fault is a VISIBLE 0.0 with the fault in ``reason`` — the
    battery completes and the thresholds fail honestly, never silently.
    """
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase, LLMTestCaseParams

    if judge is None:
        from tests.eval.subscription_judge import subscription_judge

        judge = subscription_judge()

    ask_scores: list[AskScore] = []
    for run in runs:
        for outcome in run.asks:
            case = LLMTestCase(input=outcome.ask_text, actual_output=_judged_output(outcome))
            try:
                metric = GEval(
                    name=f"arc:{outcome.ask_id}",
                    criteria=_JUDGE_PREAMBLE + outcome.criteria,
                    evaluation_params=[
                        LLMTestCaseParams.INPUT,
                        LLMTestCaseParams.ACTUAL_OUTPUT,
                    ],
                    model=judge,
                    threshold=threshold,
                    async_mode=False,  # one deterministic sync pass per ask
                )
                metric.measure(case)
                score = float(metric.score if metric.score is not None else 0.0)
                reason = str(getattr(metric, "reason", "") or "")
            except Exception as exc:  # noqa: BLE001 — a judge fault is a visible 0, never a crash
                score, reason = 0.0, f"judge/metric fault: {type(exc).__name__}: {exc}"
            ask_scores.append(
                AskScore(outcome=outcome, score=score, reason=reason, passed=score >= threshold)
            )
    return BatteryScore(runs=runs, ask_scores=ask_scores, threshold=threshold)


# ── The compact evidence report ────────────────────────────────────────────────


def render_report(score: BatteryScore) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("A-FINAL LONG-MEETING BATTERY REPORT")
    lines.append("=" * 78)
    lines.append(
        f"overall arc-correctness mean = {score.overall_mean:.3f} "
        f"(bar 0.85) — {sum(1 for s in score.ask_scores if s.passed)}/"
        f"{len(score.ask_scores)} asks >= {score.threshold}"
    )
    for stressor, mean in score.per_stressor.items():
        lines.append(f"  stressor {stressor:<16} mean = {mean:.3f} (bar 0.75)")
    for run in score.runs:
        lines.append("-" * 78)
        lines.append(
            f"scenario {run.scenario_id}: sandbox_mounted={run.sandbox_mounted} "
            f"unexpected_wakes={run.unexpected_wakes} spoken_deltas={run.spoken_deltas} "
            f"transport={list(run.transport_log)}"
        )
        for idle in run.idles:
            lines.append(f"  idle {idle.stretch_id}: {idle.lines} lines, {idle.wakes} wakes")
    lines.append("-" * 78)
    for s in score.ask_scores:
        o = s.outcome
        verdict = "PASS" if s.passed else "FAIL"
        lines.append(
            f"[{verdict}] {o.ask_id} ({o.stressor}) score={s.score:.3f} "
            f"woke={o.woke} turns={o.turns_completed}"
            + (f" group={o.concurrent_group}" if o.concurrent_group else "")
            + (f" reply_woke={o.follow_up_woke}" if o.follow_up_woke is not None else "")
        )
        lines.append(f"    ask:      {o.ask_text}")
        lines.append(f"    tools:    {list(o.tool_calls) or 'none'}")
        if o.require_transport:
            lines.append(
                f"    transport: required={list(o.require_transport)} saw={list(o.transport_verbs)}"
            )
        if o.error:
            lines.append(f"    ERROR:    {o.error}")
        lines.append(f"    response: {o.response_text[:600]!r}")
        if s.reason:
            lines.append(f"    judge:    {s.reason}")
    lines.append("=" * 78)
    return "\n".join(lines)
