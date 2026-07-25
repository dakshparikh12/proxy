"""Acceptance tests for the two-tier session-durability fallback (04 §3.5).

``resume_with_fallback`` (the pinned 6-arg form, imported from libs/agentkit)
drives the real :class:`BehaviorRunner`: Tier-1 resumes the session; on a
stale-session ``ProviderError`` it rebuilds from the history source, emits the
"session restored" notice, and retries WITHOUT resume. A caller abort is FINAL.
"""
from __future__ import annotations

import pytest

from libs.agentkit import Behavior, BehaviorConfig, BehaviorRunner, ProviderError, resume_with_fallback
from libs.agentkit.resume import RESTORED_NOTICE, STALE_MARKERS, is_stale_session_error
from libs.contracts import AgentChunk


class ScriptedProvider:
    """Yields a per-call scripted stream so we can script a stale first pass."""

    def __init__(self, per_call):
        self._per_call = list(per_call)
        self.calls = 0
        self.seen_resume: list = []

    def stream(self, prompt, query):
        idx = self.calls
        self.calls += 1
        self.seen_resume.append(query.resume)
        chunks = self._per_call[idx] if idx < len(self._per_call) else []

        async def gen():
            for ch in chunks:
                yield ch

        return gen()


class Abort:
    def __init__(self, aborted=False):
        self.aborted = aborted


def _runner(provider):
    b = Behavior(name="b", config=BehaviorConfig(name="b", tools=("speak",), model="claude-sonnet-4-6"))
    return BehaviorRunner(registry={"b": b}, provider=provider)


@pytest.mark.integration
def test_stale_markers_match_both_version_strings():
    assert is_stale_session_error(Exception("no conversation found with session id abc"))
    assert is_stale_session_error(Exception("the process exited unexpectedly"))
    assert not is_stale_session_error(Exception("some unrelated error"))
    assert "no conversation found with session id" in STALE_MARKERS
    assert "process exited" in STALE_MARKERS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resume_fallback_rebuilds_on_stale_session():
    provider = ScriptedProvider(
        per_call=[
            [AgentChunk(type="ERROR", metadata={"message": "no conversation found with session id s1"})],
            [
                AgentChunk(type="TEXT", text="recovered", metadata={"msg_id": "m2"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": "s2"}),
            ],
        ]
    )
    runner = _runner(provider)

    async def history():
        return "prior meeting turns"

    out = []
    async for ch in resume_with_fallback(runner, "b", {}, "s1", Abort(), history):
        out.append((ch.type, ch.text))

    # The user-visible restored notice precedes the rebuilt stream.
    assert (out[0][0], out[0][1]) == ("TEXT", RESTORED_NOTICE)
    assert ("TEXT", "recovered") in out
    assert out[-1][0] == "RESULT"
    # Two provider calls: the stale first, then the retry WITHOUT resume.
    assert provider.calls == 2
    assert provider.seen_resume == ["s1", None]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_caller_abort_is_final_no_resurrection():
    provider = ScriptedProvider(
        per_call=[[AgentChunk(type="ERROR", metadata={"message": "no conversation found with session id s1"})]]
    )
    runner = _runner(provider)

    async def history():
        return "should never be read"

    with pytest.raises(ProviderError):
        async for _ in resume_with_fallback(runner, "b", {}, "s1", Abort(aborted=True), history):
            pass
    # Only the first pass ran — abort short-circuits before any recovery.
    assert provider.calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_stale_error_is_not_recovered():
    provider = ScriptedProvider(
        per_call=[[AgentChunk(type="ERROR", metadata={"message": "quota exceeded"})]]
    )
    runner = _runner(provider)

    async def history():
        return "unused"

    with pytest.raises(ProviderError):
        async for _ in resume_with_fallback(runner, "b", {}, "s1", Abort(), history):
            pass
    assert provider.calls == 1, "a non-stale error is re-raised, never replayed"
