"""A-FINAL battery — the committed long-meeting verification artifact.

The founder's bar: LONG, messy, realistic meeting transcripts with MANY embedded
reactive asks, driven line-by-line through the REAL engine (real grounded code
tools over the committed ``tests/fixtures/battery_repo`` clone; a fake meeting
transport recording mute/chat; the real agent on the Claude Max subscription),
each response scored by deepeval G-Eval on the SubscriptionJudge (~$0).

Two tiers in this file:

* **Always-run (offline, hermetic)** — the scenarios parse and satisfy the
  battery's structural contract; the runner constructs against the real
  ``RepoContext`` server over the fixture clone and drives the REAL Engine with
  a scripted fake provider (no model, no judge, no network) — proving the
  committed artifact isn't rot.
* **Live (gated)** — ``A_FINAL_LIVE=1`` runs the 3 long scenarios on the real
  ``EngineProvider`` (subscription CLI auth; ``ANTHROPIC_API_KEY`` popped) and
  scores every ask with GEval on the subscription judge. Thresholds: overall
  arc-correctness mean >= 0.85; no stressor class < 0.75. The DETERMINISTIC
  invariants (zero wakes across idle stretches, transport recorded the
  mute/post_chat calls, both concurrent asks completed) are asserted as hard
  facts regardless of the judge.

Run live:  A_FINAL_LIVE=1 .venv/bin/python -m pytest tests/eval/test_a_final_battery.py -q -s
Sandbox:   add RUN_BATTERY_LIVE_E2B=1 to provision a REAL E2B sandbox for the
           sandbox-exec asks (needs E2B_API_KEY); without it no sandbox tools are
           mounted and those asks are judged on honest can't-run acknowledgment.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Sequence

import pytest

from tests.eval.meeting_battery import (
    MODEL,
    ScenarioRun,
    battery_repo_path,
    render_report,
    run_scenario,
    score_runs,
)
from tests.eval.scenarios_long_meetings import (
    BATTERY_REPO_MAP,
    STRESSOR_CLASSES,
    Ask,
    Concurrent,
    Idle,
    Line,
    MeetingScenario,
    Say,
    long_meeting_scenarios,
)

_LIVE = os.environ.get("A_FINAL_LIVE") == "1"
_LIVE_E2B = os.environ.get("RUN_BATTERY_LIVE_E2B") == "1"

_PROXY_WORD = re.compile(r"\bproxy\b", re.IGNORECASE)


# ── The scripted fake provider (offline tier only — the test_engine pattern) ──


class ScriptedProvider:
    """Replays one generic happy turn per call; records every (prompt, query)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def stream(self, prompt: str, query: object) -> AsyncIterator[object]:
        from contracts import AgentChunk

        self.calls.append((prompt, query))
        yield AgentChunk(
            type="TEXT",
            text="On it — let me check that and get back to you in a second.",
            metadata={"msg_id": f"m-{len(self.calls)}"},
        )
        yield AgentChunk(
            type="RESULT",
            text="On it — let me check that and get back to you in a second.",
            metadata={"session_id": f"s-{len(self.calls)}", "total_cost_usd": 0.0},
        )


# ── Scenario accounting helpers (independent of the runner's own math) ────────


def _line_count(scenario: MeetingScenario) -> int:
    total = 0
    for event in scenario.events:
        if isinstance(event, Say):
            total += 1
        elif isinstance(event, Idle):
            total += len(event.lines)
        elif isinstance(event, Ask):
            total += 1 + (1 if event.follow_up is not None else 0)
        elif isinstance(event, Concurrent):
            total += 2
    return total


def _speakers(scenario: MeetingScenario) -> set[str]:
    names: set[str] = set()
    for event in scenario.events:
        if isinstance(event, Say):
            names.add(event.line.speaker)
        elif isinstance(event, Idle):
            names.update(line.speaker for line in event.lines)
        elif isinstance(event, Ask):
            names.add(event.line.speaker)
        elif isinstance(event, Concurrent):
            names.add(event.first.line.speaker)
            names.add(event.second.line.speaker)
    return names


def _all_asks(scenario: MeetingScenario) -> list[Ask]:
    asks: list[Ask] = []
    for event in scenario.events:
        if isinstance(event, Ask):
            asks.append(event)
        elif isinstance(event, Concurrent):
            asks.extend((event.first, event.second))
    return asks


def _ask_line_offsets(scenario: MeetingScenario) -> list[int]:
    """The running transcript-line offset at which each ask lands."""
    offsets: list[int] = []
    cursor = 0
    for event in scenario.events:
        if isinstance(event, Say):
            cursor += 1
        elif isinstance(event, Idle):
            cursor += len(event.lines)
        elif isinstance(event, Ask):
            offsets.append(cursor)
            cursor += 1 + (1 if event.follow_up is not None else 0)
        elif isinstance(event, Concurrent):
            offsets.extend((cursor, cursor + 1))
            cursor += 2
    return offsets


# ── Always-run tier 1: the committed scenarios satisfy the battery contract ───


def test_scenarios_are_long_messy_and_cover_every_stressor_class() -> None:
    """3 scenarios, 60-120 lines each, 4-6 speakers, asks spread early/middle/
    late, every stressor class covered, and the deterministic-disambiguation
    conventions hold (ask lines address Proxy; idle/chatter lines never do)."""
    scenarios = long_meeting_scenarios()
    assert len(scenarios) == 3

    seen_stressors: set[str] = set()
    seen_ask_ids: set[str] = set()

    for scenario in scenarios:
        count = _line_count(scenario)
        assert 60 <= count <= 120, f"{scenario.id}: {count} transcript lines (want 60-120)"
        speakers = _speakers(scenario)
        assert 4 <= len(speakers) <= 6, f"{scenario.id}: {len(speakers)} speakers"

        asks = _all_asks(scenario)
        assert len(asks) >= 4, f"{scenario.id}: only {len(asks)} embedded asks"
        # Asks are spread through the meeting: one early, one late (notes grow).
        offsets = _ask_line_offsets(scenario)
        assert offsets[0] <= count * 0.4, f"{scenario.id}: first ask too late ({offsets[0]}/{count})"
        assert offsets[-1] >= count * 0.6, f"{scenario.id}: last ask too early ({offsets[-1]}/{count})"

        # At least one idle/common-noun stretch per scenario, with real 'proxy' bait.
        idles = [e for e in scenario.events if isinstance(e, Idle)]
        assert idles, f"{scenario.id}: no idle stretch"
        for idle in idles:
            assert len(idle.lines) >= 5, f"{scenario.id}/{idle.id}: idle stretch too short"
            bait = [line for line in idle.lines if _PROXY_WORD.search(line.text)]
            assert len(bait) >= 2, f"{scenario.id}/{idle.id}: needs common-noun 'proxy' bait lines"

        for ask in asks:
            assert ask.id not in seen_ask_ids, f"duplicate ask id {ask.id}"
            seen_ask_ids.add(ask.id)
            assert ask.stressor in STRESSOR_CLASSES
            seen_stressors.add(ask.stressor)
            assert ask.expect.strip(), f"{ask.id}: empty judge criteria"
            # The deterministic disambiguation convention: an addressed line STARTS
            # with the wake name; chatter never does (see meeting_battery docstring).
            assert ask.line.text.lower().lstrip().startswith("proxy"), ask.id
            if ask.stressor == "sandbox-exec":
                assert ask.expect_no_sandbox, f"{ask.id}: sandbox-exec needs a no-sandbox bar"
            if ask.stressor == "meeting-control":
                assert ask.require_transport, f"{ask.id}: meeting-control needs transport verbs"
            if ask.follow_up is not None:
                assert ask.stressor == "clarify", f"{ask.id}: follow_up is the clarify flow"
                assert not _PROXY_WORD.search(ask.follow_up.text), (
                    f"{ask.id}: the un-prefixed reply must not contain the wake word"
                )
            if ask.stressor == "clarify":
                assert ask.follow_up is not None, f"{ask.id}: clarify needs the un-prefixed reply"

        # Chatter and idle lines never start with the wake name (they may CONTAIN
        # 'proxy' as a common noun — that is the bait the trigger must decline).
        for event in scenario.events:
            plain: Sequence[Line]
            if isinstance(event, Say):
                plain = (event.line,)
            elif isinstance(event, Idle):
                plain = event.lines
            else:
                continue
            for line in plain:
                assert not line.text.lower().lstrip().startswith("proxy"), (
                    f"{scenario.id}: non-ask line starts with the wake name: {line.text!r}"
                )

        # Ask texts are unique within a scenario (turn attribution keys on them).
        texts = [a.line.text for a in asks]
        assert len(texts) == len(set(texts)), f"{scenario.id}: duplicate ask texts"

    assert seen_stressors == set(STRESSOR_CLASSES), (
        f"stressor classes missing from the battery: {set(STRESSOR_CLASSES) - seen_stressors}"
    )


def test_fixture_repo_and_map_are_committed_and_grounded() -> None:
    """The battery clone exists with the golden facts the criteria reference."""
    repo = battery_repo_path()
    assert repo.is_dir()
    files = sorted(p.name for p in repo.glob("*.py"))
    assert 6 <= len(files) <= 10, files
    retry = (repo / "retry.py").read_text(encoding="utf-8")
    assert "MAX_RETRIES = 4" in retry and "BASE_DELAY_MS = 250" in retry
    auth = (repo / "auth.py").read_text(encoding="utf-8")
    assert "except:" in auth  # the bare-except golden
    redis = (repo / "cache_redis.py").read_text(encoding="utf-8")
    assert "invalidate" not in redis  # the missing-invalidate golden
    lru = (repo / "cache_lru.py").read_text(encoding="utf-8")
    assert "def invalidate" in lru
    for name in files:
        assert name in BATTERY_REPO_MAP, f"{name} missing from the index.md map text"


# ── Always-run tier 2: the runner flows end-to-end on the scripted provider ───


@pytest.mark.asyncio
async def test_one_scripted_line_flows_through_the_real_engine() -> None:
    """The tiny rot-proof: the runner constructs (real RepoContext server over the
    committed clone, fake transport, deterministic disambiguate) and ONE scripted
    ask flows through the REAL Engine on a fake provider — no model, no judge."""
    provider = ScriptedProvider()
    scenario = MeetingScenario(
        id="offline-micro",
        title="one line flows",
        events=(
            Say(line=Line(speaker="Ana", text="Quick sync on the retry work.")),
            Ask(
                id="micro-1",
                stressor="grounded-lookup",
                line=Line(speaker="Ana", text="Proxy, what's the max retry count?"),
                expect="States MAX_RETRIES is 4.",
            ),
        ),
    )
    run = await run_scenario(scenario, provider=provider)
    assert len(run.asks) == 1
    outcome = run.asks[0]
    assert outcome.woke, "the addressed line must wake the engine"
    assert outcome.turns_completed == 1
    assert "check that" in outcome.response_text
    assert outcome.error is None
    assert len(provider.calls) == 1  # exactly one turn hit the provider
    assert run.unexpected_wakes == 0
    assert run.sandbox_mounted is False


@pytest.mark.asyncio
async def test_all_three_long_scenarios_flow_offline_with_deterministic_invariants() -> None:
    """The full committed scripts drive the real Engine + trigger + real code
    server construction on the scripted provider: every ask wakes, every idle
    stretch is ZERO wakes, clarify replies wake as un-prefixed follow-ups, and
    both concurrent asks complete via drain. No model, no judge, no network."""
    for scenario in long_meeting_scenarios():
        run = await run_scenario(scenario, provider=ScriptedProvider())
        assert run.unexpected_wakes == 0, f"{scenario.id}: chatter woke the engine"
        for idle in run.idles:
            assert idle.wakes == 0, f"{scenario.id}/{idle.stretch_id}: idle stretch woke"
        for outcome in run.asks:
            assert outcome.woke, f"{outcome.ask_id}: the ask did not wake the engine"
            assert outcome.error is None, f"{outcome.ask_id}: {outcome.error}"
            assert outcome.response_text, f"{outcome.ask_id}: no response captured"
            if outcome.stressor == "clarify":
                assert outcome.follow_up_woke is True, f"{outcome.ask_id}: reply didn't wake"
                assert outcome.turns_completed == 2
            if outcome.concurrent_group:
                assert outcome.turns_completed == 2, (
                    f"{outcome.ask_id}: both concurrent turns must complete via drain"
                )
        # The criteria actually used offline are the no-sandbox bars for exec asks.
        for outcome in run.asks:
            if outcome.stressor == "sandbox-exec":
                assert outcome.criteria and "run" in outcome.criteria.lower()


# ── The live battery (gated — the controller runs this) ───────────────────────


@pytest.mark.skipif(
    not _LIVE,
    reason="A-FINAL live battery — set A_FINAL_LIVE=1 (real engine turns on the "
    "subscription + subscription-judged scoring; many model calls)",
)
def test_a_final_battery_live() -> None:
    """The committed long-meeting battery on the REAL engine, judged end to end."""
    # Subscription CLI auth only — never the paid API (the smokes' rule).
    os.environ.pop("ANTHROPIC_API_KEY", None)

    async def _run_all() -> list[ScenarioRun]:
        runs: list[ScenarioRun] = []
        for scenario in long_meeting_scenarios():
            runs.append(await run_scenario(scenario, live_e2b=_LIVE_E2B, model=MODEL))
        return runs

    runs = asyncio.run(_run_all())

    # ── Deterministic invariants: hard facts, independent of any judge ────────
    for run in runs:
        assert run.unexpected_wakes == 0, f"{run.scenario_id}: chatter woke the engine"
        for idle in run.idles:
            assert idle.wakes == 0, (
                f"{run.scenario_id}/{idle.stretch_id}: {idle.wakes} wakes across an "
                f"idle/common-noun stretch (must be ZERO)"
            )
        for outcome in run.asks:
            assert outcome.woke, f"{outcome.ask_id}: the addressed ask did not wake"
            if outcome.concurrent_group:
                assert outcome.turns_completed == 2, (
                    f"{outcome.ask_id}: concurrent asks must BOTH complete via drain"
                )
            for verb in outcome.require_transport:
                assert verb in outcome.transport_verbs, (
                    f"{outcome.ask_id}: transport never recorded {verb!r} "
                    f"(saw {outcome.transport_verbs})"
                )

    # ── Judged arc-correctness on the subscription judge ─────────────────────
    score = score_runs(runs)
    print(render_report(score))

    assert score.overall_mean >= 0.85, (
        f"A-FINAL overall arc-correctness mean {score.overall_mean:.3f} < 0.85"
    )
    for stressor, mean in sorted(score.per_stressor.items()):
        assert mean >= 0.75, f"stressor class {stressor!r} mean {mean:.3f} < 0.75"
