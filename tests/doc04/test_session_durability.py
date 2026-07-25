"""Acceptance tests for two-tier session durability (04 §3.5, node
``orchestrator.session-durability``).

These exercise the parts of §3.5 the pinned 6-arg ``resume_with_fallback`` owns
beyond the base stale-session replay already covered by
``test_runner_resume_fallback.py``:

  * **Tier 1** — the session_id is captured from the first wake's ``INIT`` chunk
    and passed as ``resume=<session_id>`` on the next wake (persist-from-INIT +
    resume-per-wake). Proven by driving the real :class:`BehaviorRunner` across
    two wakes and reading what the provider saw.
  * **Tier 3** — a wake that lands on a different instance after a recycle:
    ``resume`` fails with a stale-session marker, the fallback rebuilds from the
    Doc 03 transcript-plane ``history_fn``, emits the RESTORED_NOTICE, and retries
    WITHOUT resume — and the rebuilt turn answers *coherently* (the prior-meeting
    transcript reaches the model as a delimited preamble).
  * **JSON-truncation retry (cap 2, same session)** — the SDK stdio pipe can
    truncate a large tool-result frame → ``SyntaxError: unterminated string in
    json``. That is NOT a gone session: retry on the SAME session (resume
    unchanged), capped at 2 attempts, then surface the error. Abort is final and
    short-circuits before this retry too.

All drive the real ``resume_with_fallback`` + ``BehaviorRunner`` + delta stream;
only the provider is scripted (its ``stream`` is the injected seam).
"""
from __future__ import annotations

import pytest

from libs.agentkit import (
    Behavior,
    BehaviorConfig,
    BehaviorRunner,
    ProviderError,
    resume_with_fallback,
)
from libs.agentkit.resume import RESTORED_NOTICE, build_history_preamble
from libs.contracts import AgentChunk


class ScriptedProvider:
    """Yields a per-call scripted ``AgentChunk`` stream and records what each call
    saw (the resume pointer + the rendered prompt), so a test can assert the
    Tier-1 resume flow and the Tier-3 preamble-carries-history behavior."""

    def __init__(self, per_call):
        self._per_call = list(per_call)
        self.calls = 0
        self.seen_resume: list = []
        self.seen_prompts: list[str] = []

    def stream(self, prompt, query):
        idx = self.calls
        self.calls += 1
        self.seen_resume.append(query.resume)
        self.seen_prompts.append(prompt)
        chunks = self._per_call[idx] if idx < len(self._per_call) else []

        async def gen():
            for ch in chunks:
                yield ch

        return gen()


class Abort:
    def __init__(self, aborted=False):
        self.aborted = aborted


def _runner(provider):
    b = Behavior(
        name="answer",
        config=BehaviorConfig(
            name="answer",
            role="answer-question",
            tools=("speak",),
            model="claude-sonnet-4-6",
            inputs=("question",),
        ),
    )
    return BehaviorRunner(registry={"answer": b}, provider=provider)


async def _drain(agen):
    out = []
    async for ch in agen:
        out.append(ch)
    return out


# ---------------------------------------------------------------------------
# Tier 1 — persist session_id from INIT, resume each wake
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_tier1_session_id_captured_from_init_and_resumed_next_wake():
    """The first wake carries no resume; its INIT chunk exposes the SDK session_id,
    which the harness persists and hands back as ``resume`` on the next wake."""
    provider = ScriptedProvider(
        per_call=[
            [
                AgentChunk(type="INIT", metadata={"session_id": "sdk-sess-1"}),
                AgentChunk(type="TEXT", text="first answer", metadata={"msg_id": "m1"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": "sdk-sess-1"}),
            ],
            [
                AgentChunk(type="TEXT", text="second answer", metadata={"msg_id": "m2"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": "sdk-sess-1"}),
            ],
        ]
    )
    runner = _runner(provider)

    async def history():  # not reached on the happy Tier-1 path
        raise AssertionError("history_fn must not be read when resume succeeds")

    # First wake: no persisted session yet → resume is None. Capture session_id from INIT.
    first = await _drain(resume_with_fallback(runner, "answer", {"question": "q1"}, None, Abort(), history))
    init = next(c for c in first if c.type == "INIT")
    persisted_session_id = init.metadata["session_id"]
    assert persisted_session_id == "sdk-sess-1"

    # Second wake: the harness resumes the persisted session_id (same instance still alive).
    second = await _drain(
        resume_with_fallback(runner, "answer", {"question": "q2"}, persisted_session_id, Abort(), history)
    )
    assert ("second answer") in [c.text for c in second if c.type == "TEXT"]

    # The provider saw: wake-1 with no resume, wake-2 resuming the captured session_id.
    assert provider.seen_resume == [None, "sdk-sess-1"]
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# Tier 3 — wake-after-instance-swap replays from the transcript plane, coherently
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_tier3_instance_swap_replays_from_transcript_plane_coherently():
    """A recycle lands the wake on a new instance; resume fails stale; the fallback
    rebuilds from Doc 03's transcript plane, emits the notice, retries WITHOUT
    resume, and the rebuilt turn's prompt CARRIES the prior meeting (coherent replay)."""
    prior_meeting = "Alice: let's ship Friday.\nBob: the migration must land first."
    provider = ScriptedProvider(
        per_call=[
            # New instance: the resumed session is gone.
            [AgentChunk(type="ERROR", metadata={"message": "no conversation found with session id sdk-sess-1"})],
            # Retry without resume, context rebuilt from history → a coherent answer.
            [
                AgentChunk(type="TEXT", text="Right — the migration lands before Friday.", metadata={"msg_id": "m2"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.02, "num_turns": 1, "session_id": "sdk-sess-2"}),
            ],
        ]
    )
    runner = _runner(provider)

    history_reads = {"n": 0}

    async def history():
        history_reads["n"] += 1
        return prior_meeting

    out = await _drain(
        resume_with_fallback(runner, "answer", {"question": "what's the plan?"}, "sdk-sess-1", Abort(), history)
    )
    texts = [c.text for c in out if c.type == "TEXT"]

    # Transparent, not silent: the restored notice precedes the rebuilt answer.
    assert texts[0] == RESTORED_NOTICE
    assert "the migration lands before Friday" in texts[1]

    # The transcript plane was read exactly once and reached the retry as a preamble.
    assert history_reads["n"] == 1
    retry_prompt = provider.seen_prompts[1]
    assert build_history_preamble(prior_meeting) in retry_prompt
    assert "Bob: the migration must land first." in retry_prompt

    # Two provider calls: stale (with resume), then the coherent retry WITHOUT resume.
    assert provider.seen_resume == ["sdk-sess-1", None]
    assert provider.calls == 2


# ---------------------------------------------------------------------------
# JSON-truncation retry — cap 2, SAME session (not a gone session)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_json_truncation_retries_on_same_session():
    """A truncated stdio frame (``unterminated string in json``) is transient, not a
    gone session: retry on the SAME session (resume unchanged), and succeed."""
    provider = ScriptedProvider(
        per_call=[
            [AgentChunk(type="ERROR", metadata={"message": "SyntaxError: unterminated string in json"})],
            [
                AgentChunk(type="TEXT", text="recovered on retry", metadata={"msg_id": "m1"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": "s1"}),
            ],
        ]
    )
    runner = _runner(provider)

    async def history():
        raise AssertionError("a JSON-truncation retry stays on the same session — never rebuilds from history")

    out = await _drain(resume_with_fallback(runner, "answer", {"question": "q"}, "s1", Abort(), history))
    texts = [c.text for c in out if c.type == "TEXT"]

    # No restored notice — this was NOT a session loss, just a truncated frame.
    assert RESTORED_NOTICE not in texts
    assert "recovered on retry" in texts

    # The retry stayed on the SAME session (resume unchanged), unlike the stale-session tier.
    assert provider.seen_resume == ["s1", "s1"]
    assert provider.calls == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_json_truncation_retry_is_capped_at_two_attempts():
    """A frame that keeps truncating is not retried forever — the cap is 2 attempts,
    after which the error surfaces as a ProviderError (never an infinite loop)."""
    truncated = [AgentChunk(type="ERROR", metadata={"message": "unterminated string in json"})]
    provider = ScriptedProvider(per_call=[truncated, truncated, truncated, truncated])
    runner = _runner(provider)

    async def history():
        raise AssertionError("history is never read for a JSON-truncation error")

    with pytest.raises(ProviderError):
        await _drain(resume_with_fallback(runner, "answer", {"question": "q"}, "s1", Abort(), history))

    # Capped: the initial attempt plus at most 2 retries = 3 provider calls, no runaway.
    assert provider.calls <= 3
    assert provider.calls >= 2, "at least one retry was attempted before giving up"
    assert set(provider.seen_resume) == {"s1"}, "every capped retry stayed on the same session"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_json_truncation_abort_is_final_no_retry():
    """Abort is final: a caller-killed turn is not resurrected by the JSON-truncation
    retry any more than by the stale-session replay."""
    provider = ScriptedProvider(
        per_call=[[AgentChunk(type="ERROR", metadata={"message": "unterminated string in json"})]]
    )
    runner = _runner(provider)

    async def history():
        raise AssertionError("aborted turn: no recovery of any kind")

    with pytest.raises(ProviderError):
        await _drain(resume_with_fallback(runner, "answer", {"question": "q"}, "s1", Abort(aborted=True), history))
    # Only the first pass ran — abort short-circuits before the JSON retry.
    assert provider.calls == 1
