"""Acceptance — AbortController discipline (04 §3.11, CANONICAL §11.9).

The four things §3.11 promises, tested on the real path:

  1. **Abort halts the MODEL loop.** A ``query_fn`` that yields forever is stopped
     PROMPTLY once its ``AbortController`` fires — the provider seam breaks the
     ``async for message`` loop, it does NOT merely ignore the result after the
     stream drains (the SDK would otherwise run to ``maxTurns=1000``, burning budget).
  2. **A new judgment-moment preempts a stale one.** ``make(key)`` on a live key
     cancels/aborts the prior controller for that key (stale-judgment preemption).
  3. **Meeting-end kills everything.** ``cancel_meeting(meeting_id)`` aborts every
     controller keyed ``meeting_id|task_id`` for that meeting, and only that meeting.
  4. **Abort is FINAL.** An aborted turn is never resurrected by resume / JSON-retry —
     ``resume_with_fallback`` short-circuits before EITHER §3.5 recovery arm.

The registry is imported from ``libs/agentkit/abort.py`` — never redefined (§11.9).
"""
from __future__ import annotations

import asyncio

import pytest

from libs.agentkit import AbortRegistry
from libs.agentkit.abort import AbortController
from libs.agentkit.resume import resume_with_fallback
from libs.contracts import AgentChunk


# ---------------------------------------------------------------------------
# 1 — abort halts the model loop (the maxTurns=1000 runaway-spend fix)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_abort_halts_the_model_loop_not_just_the_result():
    """A forever-yielding query_fn is stopped PROMPTLY once the controller aborts.

    Proves the provider breaks the SDK ``async for message`` loop mid-stream — not
    that it ran to completion and then dropped the result on the floor.
    """
    from harness.provider import ClaudeAgentProvider
    from libs.agentkit import ProviderQuery

    controller = AbortController()
    yielded = 0
    drained_to_end = False

    async def forever_query_fn(*, prompt, options):  # noqa: ARG001 — SDK signature
        nonlocal yielded, drained_to_end
        # A ResultMessage-shaped object would end the turn; instead yield an unbounded
        # stream of assistant-shaped messages — a runaway model loop. If the provider
        # only "ignored the result" this loop would spin to exhaustion (never here).
        from claude_agent_sdk import AssistantMessage, TextBlock

        i = 0
        while True:
            i += 1
            yielded = i
            # Fire the abort from *inside* the stream after a few turns: this models the
            # human saying "Proxy, quiet" while the model is mid-run.
            if i == 3:
                controller.abort()
            yield AssistantMessage(
                content=[TextBlock(text=f"turn {i}")],
                model="claude-opus-4-8",
            )
            await asyncio.sleep(0)  # give the loop a chance to run away if unguarded
            if i > 5000:
                drained_to_end = True  # a guarded loop must NEVER reach here
                return

    provider = ClaudeAgentProvider(query_fn=forever_query_fn, sandbox_mode=False)
    query = ProviderQuery(model="claude-opus-4-8", allowed_tools=("speak",), abort=controller, max_turns=1000)

    chunks: list[AgentChunk] = []
    # A guarded loop returns promptly; wrap in a timeout so a runaway loop FAILS loud.
    async def _drive():
        async for chunk in provider.stream("prompt", query):
            chunks.append(chunk)

    await asyncio.wait_for(_drive(), timeout=2.0)

    assert not drained_to_end, "the model loop ran away to exhaustion — abort did not halt it"
    assert yielded < 50, f"stream kept pulling messages long after abort (pulled {yielded})"
    # It halted mid-run: we saw the pre-abort turns, not thousands of them.
    assert any(c.type == "TEXT" for c in chunks)


# ---------------------------------------------------------------------------
# 2 — make() preempts a stale controller for the same key
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_make_preempts_the_prior_controller_for_a_live_key():
    """A fresh judgment-moment cancels the stale one: make() aborts the prior handle."""
    reg = AbortRegistry()
    key = "m1|t1"
    stale = reg.make(key)
    assert stale.aborted is False

    fresh = reg.make(key)  # a new judgment-moment for the same task
    assert stale.aborted is True, "make() must abort/preempt the prior controller"
    assert fresh.aborted is False, "the fresh controller is live"
    assert fresh is not stale
    # The registry now hands out the fresh one for that key.
    assert reg.get(key) is fresh


@pytest.mark.integration
@pytest.mark.asyncio
async def test_controller_wait_unblocks_on_abort():
    """``await controller.wait()`` returns once the controller is aborted."""
    controller = AbortController()

    async def _aborter():
        await asyncio.sleep(0.01)
        controller.abort()

    task = asyncio.ensure_future(_aborter())
    await asyncio.wait_for(controller.wait(), timeout=1.0)
    assert controller.aborted is True
    await task


# ---------------------------------------------------------------------------
# 3 — cancel_meeting kills all of a meeting's task controllers (and only those)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cancel_meeting_aborts_every_task_of_that_meeting_only():
    reg = AbortRegistry()
    a1 = reg.make("meetingA|task1")
    a2 = reg.make("meetingA|task2")
    b1 = reg.make("meetingB|task1")

    reg.cancel_meeting("meetingA")

    assert a1.aborted is True
    assert a2.aborted is True
    assert b1.aborted is False, "another meeting's tasks are untouched (isolation)"


@pytest.mark.integration
def test_cancel_meeting_does_not_false_match_a_prefix():
    """``meetingA`` must not cancel ``meetingA2``'s tasks — keys split on the delimiter."""
    reg = AbortRegistry()
    a = reg.make("meetingA|t1")
    a2 = reg.make("meetingA2|t1")

    reg.cancel_meeting("meetingA")

    assert a.aborted is True
    assert a2.aborted is False, "a longer meeting id sharing a prefix must not be cancelled"


# ---------------------------------------------------------------------------
# 4 — an aborted turn is not resurrected by resume / JSON-retry
# ---------------------------------------------------------------------------

class _StaleThenSpyRunner:
    """First pass raises a gone-session ProviderError; a SECOND pass would mean the
    aborted build was resurrected (the test asserts it never runs)."""

    def __init__(self) -> None:
        self.passes = 0

    def run(self, behavior, inputs, abort):  # noqa: ARG002
        self.passes += 1
        this_pass = self.passes

        async def gen():
            if this_pass == 1:
                from libs.agentkit.provider import ProviderError

                raise ProviderError(
                    AgentChunk(type="ERROR", metadata={"message": "No conversation found with session id abc"})
                )
            # A second pass means resume resurrected an aborted build — forbidden.
            raise AssertionError("aborted turn was resurrected by resume")
            yield  # pragma: no cover

        return gen()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aborted_turn_is_not_resurrected_by_resume():
    """A live AbortController on an aborted turn short-circuits §3.5 recovery."""
    runner = _StaleThenSpyRunner()
    aborted = AbortRegistry().make("m|t")
    aborted.abort()

    async def history():
        raise AssertionError("history rebuild ran — aborted build was resurrected")

    with pytest.raises(Exception):  # noqa: PT011 — the ProviderError re-raises through
        async for _ in resume_with_fallback(runner, "b", {}, "s1", aborted, history):
            pass

    assert runner.passes == 1, "only the first pass ran — abort is final, no recovery"
