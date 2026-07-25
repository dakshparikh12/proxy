"""Acceptance tests for node ``orchestrator.behavior-runner`` (04 §3.4/§3.3/§3.13/§3.5).

The real generic :class:`BehaviorRunner` is exercised end to end against an
injected fake provider seam (the standard seam-test pattern in this tree — the
concrete SDK provider is a confirm-at-build item, D-010). Each test asserts one
node clause:

  * mounts EXACTLY the declared curated tool subset (``allowed_tools =
    config.tools`` — §10.5, never the union);
  * computes the SDK-isolation triad params (``strict_mcp_config=True``,
    ``setting_sources=[]``, a computed built-in ``tools`` list = ``[]`` in sandbox
    mode);
  * streams through the provider seam and applies ``stream_deltas`` exactly once
    (per-``msg_id`` suffix on TEXT, non-TEXT passed through);
  * the cost meter observes ``RESULT.metadata["total_cost_usd"]`` off that same
    delta stream (metered as a consumer, not inside the delta computer);
  * a pass-through ``ERROR`` chunk raises ``ProviderError`` at the runner boundary;
  * targeted extended thinking is ON only for the Opus grounded-answer / Workroom
    plan turns and OFF on the fast paths, budget capped below MAX_OUTPUT_TOKENS;
  * selecting a behavior by NAME is the branch — no per-behavior code path.
"""
from __future__ import annotations

import inspect

import pytest

from libs.agentkit import (
    Behavior,
    BehaviorConfig,
    BehaviorRunner,
    ProviderError,
    ProviderQuery,
    compute_builtin_tools,
    thinking_policy,
)
from libs.agentkit.provider import EXTENDED_THINKING_BUDGET_TOKENS, MAX_OUTPUT_TOKENS
from libs.contracts import AgentChunk


# ── fake provider seam (records the query, yields a scripted async stream) ──
class FakeProvider:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.seen_query: ProviderQuery | None = None
        self.seen_prompt: str | None = None
        self.calls = 0

    def stream(self, prompt, query):
        self.calls += 1
        self.seen_prompt = prompt
        self.seen_query = query

        async def gen():
            for ch in self._chunks:
                yield ch

        return gen()


class RecordingMeter:
    def __init__(self):
        self.observed: list[AgentChunk] = []

    def observe(self, chunk: AgentChunk) -> None:
        self.observed.append(chunk)

    @property
    def result_costs(self):
        return [c.metadata.get("total_cost_usd") for c in self.observed if c.type == "RESULT"]


def _answer_behavior():
    cfg = BehaviorConfig(
        name="answer-question",
        tools=("get_dependents", "who_writes", "grep", "read", "batch_read", "speak", "send_chat"),
        model="claude-sonnet-4-6",
        role="answer-question",
        max_turns=4,
    )
    return Behavior(name="answer-question", config=cfg, role="answer-question")


def _grounded_opus_behavior():
    cfg = BehaviorConfig(
        name="grounded-answer",
        tools=("get_dependents", "read", "speak"),
        model="claude-opus-4-8",
        role="grounded-answer",
        max_turns=2,
    )
    return Behavior(name="grounded-answer", config=cfg, role="grounded-answer")


def _runner(behavior, provider, meter=None):
    return BehaviorRunner(
        registry={behavior.name: behavior}, provider=provider, cost_meter=meter
    )


async def _collect(runner, name, inputs=None):
    out = []
    async for ch in runner.run(name, inputs or {}):
        out.append(ch)
    return out


# ── clause 1: curated tool subset, never the union ─────────────────────────
@pytest.mark.integration
@pytest.mark.asyncio
async def test_behavior_runner_mounts_curated_subset_never_union():
    b = _answer_behavior()
    fp = FakeProvider([AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})])
    r = _runner(b, fp)
    await _collect(r, "answer-question")
    # allowed_tools is EXACTLY config.tools — the declared curated subset.
    assert tuple(fp.seen_query.allowed_tools) == b.config.tools
    # It is exactly the declared subset, not the union with a DISJOINT behavior's
    # tools (which would be strictly larger — the anti-pattern §10.5 forbids).
    other = BehaviorConfig(name="propose-action", tools=("dispatch_workroom", "show_screen"), model="claude-sonnet-4-6")
    union = set(b.config.tools) | set(other.tools)
    assert union > set(b.config.tools), "the union is strictly larger than the curated subset"
    assert set(fp.seen_query.allowed_tools) != union, "must NOT mount the union of tools"
    assert set(fp.seen_query.allowed_tools) == set(b.config.tools)


@pytest.mark.integration
def test_curated_tools_config_field_is_the_mount_source():
    # allowed_tools = config.tools (the D-016 envelope); mounted_tools resolves it.
    cfg = BehaviorConfig(name="x", tools=("a", "b"), model="m")
    assert cfg.mounted_tools == ("a", "b")
    # Legacy allowed_tools populates the mount when tools is empty, never a union.
    legacy = BehaviorConfig(name="y", allowed_tools=("read",), model="m")
    assert legacy.mounted_tools == ("read",)


# ── clause 2: SDK-isolation triad params ───────────────────────────────────
@pytest.mark.integration
@pytest.mark.asyncio
async def test_behavior_runner_computes_isolation_triad():
    b = _answer_behavior()
    fp = FakeProvider([AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})])
    r = _runner(b, fp)
    await _collect(r, "answer-question")
    q = fp.seen_query
    assert q.strict_mcp_config is True, "strict_mcp_config must be True (ignore discovered .mcp.json/connectors)"
    assert tuple(q.setting_sources) == (), "setting_sources must be [] (load no fs settings/hooks/CLAUDE.md)"
    assert tuple(q.tools) == (), "computed built-in tools must be [] in sandbox mode (no host Read/Grep/Bash)"


@pytest.mark.integration
def test_compute_builtin_tools_is_empty_in_sandbox_mode():
    assert compute_builtin_tools(("grep", "read", "speak")) == ()
    assert compute_builtin_tools([]) == ()


# ── clause 3: stream through the seam, stream_deltas applied exactly once ───
@pytest.mark.integration
@pytest.mark.asyncio
async def test_behavior_runner_deltaizes_text_once_passes_through_rest():
    b = _answer_behavior()
    chunks = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        AgentChunk(type="TEXT", text="He", metadata={"msg_id": "m1"}),
        AgentChunk(type="TEXT", text="Hello", metadata={"msg_id": "m1"}),
        AgentChunk(type="TOOL_USE", metadata={"id": "t1", "name": "read", "input": {}}),
        AgentChunk(type="TEXT", text="Bye", metadata={"msg_id": "m2"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.02, "num_turns": 1, "session_id": "s1"}),
    ]
    r = _runner(b, FakeProvider(chunks))
    out = await _collect(r, "answer-question")
    texts = [c.text for c in out if c.type == "TEXT"]
    assert texts == ["He", "llo", "Bye"], "TEXT must be per-msg_id suffix deltas (delta-ized exactly once)"
    passthrough = [c.type for c in out if c.type != "TEXT"]
    assert passthrough == ["INIT", "TOOL_USE", "RESULT"], "non-TEXT chunks pass through unchanged and in order"


@pytest.mark.static_
def test_stream_deltas_applied_exactly_once_in_execution_module():
    # AC-CMP-005 discipline mirrored at the node: the single stream_deltas() call
    # token lives in execution.py alongside class BehaviorRunner.
    from pathlib import Path

    src = Path("libs/agentkit/src/agentkit/execution.py").read_text(encoding="utf-8")
    assert src.count("stream_deltas(") == 1, "stream_deltas() must be applied exactly once in the runner"
    assert "class BehaviorRunner" in src


# ── clause 4: cost meter observes RESULT.total_cost_usd off the delta stream ─
@pytest.mark.integration
@pytest.mark.asyncio
async def test_cost_meter_reads_result_total_cost_off_the_delta_stream():
    b = _answer_behavior()
    chunks = [
        AgentChunk(type="TEXT", text="Hi", metadata={"msg_id": "m1"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0137, "num_turns": 2, "session_id": "s1"}),
    ]
    meter = RecordingMeter()
    r = _runner(b, FakeProvider(chunks), meter)
    out = await _collect(r, "answer-question")
    assert meter.result_costs == [0.0137], "meter must observe RESULT.total_cost_usd off the delta stream"
    # The meter observes the SAME typed stream the caller yields (it is a consumer,
    # not a re-wrap): every yielded chunk was also observed.
    observed_types = [c.type for c in meter.observed]
    yielded_types = [c.type for c in out]
    assert yielded_types == observed_types, "meter observes the same delta stream the consumer yields"


# ── clause 5: ERROR chunk raises ProviderError at the runner boundary ───────
@pytest.mark.integration
@pytest.mark.asyncio
async def test_error_chunk_raises_providererror_at_boundary():
    b = _answer_behavior()
    chunks = [
        AgentChunk(type="TEXT", text="partial", metadata={"msg_id": "m1"}),
        AgentChunk(type="ERROR", metadata={"message": "boom"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"}),
    ]
    r = _runner(b, FakeProvider(chunks))
    seen = []
    with pytest.raises(ProviderError) as ei:
        async for ch in r.run("answer-question", {}):
            seen.append(ch.type)
    assert [t for t in seen] == ["TEXT"], "chunks before ERROR are yielded; ERROR is not"
    assert ei.value.chunk.metadata.get("message") == "boom"
    assert str(ei.value) == "boom"


# ── clause 6: targeted extended thinking (ON opus grounded/plan; OFF fast) ──
@pytest.mark.integration
def test_thinking_policy_on_only_for_opus_grounded_and_plan():
    on_grounded, budget_g = thinking_policy("claude-opus-4-8", "grounded-answer")
    on_plan, budget_p = thinking_policy("claude-opus-4-8", "plan-artifact")
    assert on_grounded is True and on_plan is True
    assert 0 < budget_g <= MAX_OUTPUT_TOKENS // 4 <= MAX_OUTPUT_TOKENS
    assert budget_g == min(EXTENDED_THINKING_BUDGET_TOKENS, MAX_OUTPUT_TOKENS // 4)
    # OFF on the fast paths: should-I-speak gate, quick lookups, scribe, catchup.
    for role in ("answer-question", "catchup", "should-i-speak", "quick"):
        off, budget = thinking_policy("claude-sonnet-4-6", role)
        assert off is False and budget == 0, f"thinking must be OFF for fast path {role!r}"
    # OFF even for a reasoning role when the model is not the Opus reasoning tier.
    off_model, _ = thinking_policy("claude-haiku-4-5", "grounded-answer")
    assert off_model is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_query_enables_thinking_for_opus_grounded_turn():
    b = _grounded_opus_behavior()
    fp = FakeProvider([AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})])
    r = _runner(b, fp)
    await _collect(r, "grounded-answer")
    assert fp.seen_query.thinking_enabled is True
    assert 0 < fp.seen_query.thinking_budget_tokens <= MAX_OUTPUT_TOKENS
    # And OFF for the sonnet answer-question turn.
    b2 = _answer_behavior()
    fp2 = FakeProvider([AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})])
    r2 = _runner(b2, fp2)
    await _collect(r2, "answer-question")
    assert fp2.seen_query.thinking_enabled is False
    assert fp2.seen_query.thinking_budget_tokens == 0


# ── clause 7: selecting a behavior by NAME is the branch (no per-behavior code) ─
@pytest.mark.integration
@pytest.mark.asyncio
async def test_selection_is_by_name_no_per_behavior_branch():
    # Two different behaviors run through the IDENTICAL runner path — only the name
    # (and thus the declared config) differs; no per-behavior code branch exists.
    answer = _answer_behavior()
    catchup = Behavior(
        name="catchup",
        config=BehaviorConfig(name="catchup", tools=("speak", "send_chat"), model="claude-sonnet-4-6", role="catchup"),
        role="catchup",
    )
    fp = FakeProvider([AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})])
    runner = BehaviorRunner(registry={"answer-question": answer, "catchup": catchup}, provider=fp)
    await _collect(runner, "answer-question")
    assert set(fp.seen_query.allowed_tools) == set(answer.config.tools)
    await _collect(runner, "catchup")
    assert set(fp.seen_query.allowed_tools) == set(catchup.config.tools)


@pytest.mark.static_
def test_runner_has_no_per_behavior_conditional_branch():
    # No if/elif keyed on a behavior name string literal in the runner body.
    from pathlib import Path

    src = Path("libs/agentkit/src/agentkit/execution.py").read_text(encoding="utf-8")
    for forbidden in ('== "answer-question"', "== 'answer-question'", '== "catchup"', "fire_behavior"):
        assert forbidden not in src, f"runner must not branch per behavior: found {forbidden!r}"


@pytest.mark.integration
def test_run_is_async_generator():
    assert inspect.isasyncgenfunction(BehaviorRunner.run)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_behavior_name_is_a_keyerror():
    fp = FakeProvider([])
    runner = BehaviorRunner(registry={}, provider=fp)
    with pytest.raises(KeyError):
        async for _ in runner.run("nope", {}):
            pass
