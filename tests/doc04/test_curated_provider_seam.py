"""Acceptance tests for the provider seam contract + curated-tool discipline
(04 §3.3/§3.4, CANONICAL §10.5) that the BehaviorRunner streams through.
"""
from __future__ import annotations

import pytest

from libs.agentkit import (
    BehaviorConfig,
    BehaviorRunner,
    Provider,
    ProviderQuery,
    compute_builtin_tools,
    pick_provider,
    register_provider,
)
from libs.agentkit.provider import SDK_LOCAL_TOOLS, disallowed_tools, permission_mode
from libs.contracts import AgentChunk


class _FakeProvider:
    def stream(self, prompt, query):
        async def gen():
            yield AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.0, "num_turns": 1, "session_id": "s"})

        return gen()


@pytest.mark.integration
def test_provider_query_defaults_are_the_safe_isolation_triad():
    q = ProviderQuery(model="m", allowed_tools=("speak",))
    assert q.strict_mcp_config is True
    assert tuple(q.setting_sources) == ()
    assert tuple(q.tools) == ()


@pytest.mark.integration
def test_curated_subset_is_never_the_union():
    answer = BehaviorConfig(name="answer", tools=("read", "grep", "speak"), model="m")
    propose = BehaviorConfig(name="propose-action", tools=("dispatch_workroom",), model="m")
    catchup = BehaviorConfig(name="catchup", tools=("speak",), model="m")
    union = set(answer.tools) | set(propose.tools) | set(catchup.tools)
    assert union > set(answer.tools), "the whole-Proxy union is strictly larger"
    assert set(answer.mounted_tools) != union, "a behavior mounts its subset, never the union"
    assert set(answer.mounted_tools) == set(answer.tools)
    # A leaner behavior advertises strictly fewer tools than a richer one.
    assert set(catchup.mounted_tools) < set(answer.mounted_tools)


@pytest.mark.integration
def test_compute_builtin_tools_never_advertises_host_builtins():
    got = compute_builtin_tools(("Read", "Grep", "Bash", "read", "speak"))
    assert got == (), "seam calls advertise no host built-ins (isolation floor)"
    # The disallowed host tools the seam names are exactly the SDK-local block-list.
    assert set(SDK_LOCAL_TOOLS) == set(disallowed_tools)
    assert isinstance(permission_mode, str)


@pytest.mark.integration
def test_provider_registry_picks_by_model_and_falls_back_to_default():
    fp = _FakeProvider()
    register_provider(fp, models=("test-model-xyz",))
    assert pick_provider("test-model-xyz") is fp
    # An unregistered model falls back to a default (some provider is registered).
    assert isinstance(pick_provider("unregistered-model-abc"), Provider)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_streams_through_the_seam_via_registry():
    # No explicit provider: the runner resolves one from the seam registry by model.
    fp = _FakeProvider()
    register_provider(fp, models=("seam-model-1",))
    b = BehaviorConfig(name="b", tools=("speak",), model="seam-model-1")
    runner = BehaviorRunner(registry={"b": None} and {}, provider=None)
    runner = BehaviorRunner(config=b)  # single-behavior convenience; no injected provider
    out = []
    async for ch in runner.run(None, {}):
        out.append(ch.type)
    assert out == ["RESULT"], "runner streamed through the registry-resolved provider seam"
