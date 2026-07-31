"""Offline unit tests for the subscription deepeval judge (task JUDGE, AC #1).

The judge (``tests/eval/subscription_judge.py``) is a custom ``DeepEvalBaseLLM``
that drives the Claude Agent SDK on the Claude Max SUBSCRIPTION (no API key).
These tests run OFFLINE: the SDK ``query`` seam is injected with a fake
async-generator yielding SDK-shaped messages — no network, no subscription.

Skips cleanly when deepeval is not installed (the ``eval`` dependency group is
opt-in; run ``uv sync --group eval`` — same pattern as ``test_smoke_eval.py``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any, Callable

import pytest

pytest.importorskip("deepeval", reason="deepeval not installed; run `uv sync --group eval` to opt in")

from pydantic import BaseModel

from tests.eval.subscription_judge import (
    SubscriptionJudge,
    SubscriptionJudgeError,
    subscription_judge,
)


class _S(BaseModel):
    """The G-Eval-shaped verdict schema ({score, reason})."""

    score: float
    reason: str


# ---------------------------------------------------------------------------
# Fake SDK-shaped messages (duck-typed like claude_agent_sdk.types)
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAssistantMessage:
    """AssistantMessage-like: ``.content`` is a list of TextBlock-like objects."""

    def __init__(self, texts: list[str]) -> None:
        self.content = [_FakeTextBlock(t) for t in texts]


class _FakeResultMessage:
    """ResultMessage-like: ``.result`` carries the final text."""

    def __init__(self, result: str) -> None:
        self.result = result


class _FakeSystemMessage:
    """SystemMessage/RateLimitEvent stand-in: no text-bearing attrs — must be ignored."""


def _fake_query(messages: list[Any]) -> Callable[[str], AsyncIterator[Any]]:
    """Build an injectable fake ``query_fn`` yielding the given message sequence."""

    async def _q(prompt: str) -> AsyncIterator[Any]:
        assert isinstance(prompt, str) and prompt
        for message in messages:
            yield message

    return _q


# ---------------------------------------------------------------------------
# a_generate — raw text
# ---------------------------------------------------------------------------


def test_a_generate_returns_result_text() -> None:
    """a_generate returns the final ResultMessage.result text (no duplication with assistant text)."""
    judge = subscription_judge(
        query_fn=_fake_query(
            [
                _FakeSystemMessage(),
                _FakeAssistantMessage(["judged: fine"]),
                _FakeResultMessage("judged: fine"),
            ]
        )
    )
    out = asyncio.run(judge.a_generate("score this"))
    assert out == "judged: fine"


def test_a_generate_falls_back_to_assistant_text() -> None:
    """Without a ResultMessage, the concatenated AssistantMessage TextBlock texts are returned."""
    judge = subscription_judge(query_fn=_fake_query([_FakeAssistantMessage(["part one", "part two"])]))
    out = asyncio.run(judge.a_generate("score this"))
    assert isinstance(out, str)
    assert "part one" in out
    assert "part two" in out


# ---------------------------------------------------------------------------
# a_generate — schema parse (what G-Eval drives)
# ---------------------------------------------------------------------------


def test_a_generate_with_schema_returns_pydantic_instance() -> None:
    judge = subscription_judge(query_fn=_fake_query([_FakeResultMessage('{"score": 0.9, "reason": "ok"}')]))
    got = asyncio.run(judge.a_generate("score this", schema=_S))
    assert isinstance(got, _S)
    assert got.score == 0.9
    assert got.reason == "ok"


def test_schema_extraction_survives_fences_and_prose() -> None:
    """The model may wrap the JSON in markdown fences / prose — extraction must be robust."""
    wrapped = 'Here is my verdict:\n```json\n{"score": 0.5, "reason": "meh"}\n```\nthanks'
    judge = subscription_judge(query_fn=_fake_query([_FakeResultMessage(wrapped)]))
    got = asyncio.run(judge.a_generate("score this", schema=_S))
    assert isinstance(got, _S)
    assert got.score == 0.5
    assert got.reason == "meh"


def test_schema_mismatch_surfaces_clear_error() -> None:
    """A verdict that parses as JSON but does not fit the schema raises the judge error (never a silent 0)."""
    judge = subscription_judge(query_fn=_fake_query([_FakeResultMessage('{"verdict": "yes"}')]))
    with pytest.raises(SubscriptionJudgeError):
        asyncio.run(judge.a_generate("score this", schema=_S))


# ---------------------------------------------------------------------------
# generate (sync) — must work from BOTH sync and async callers
# ---------------------------------------------------------------------------


def test_generate_sync_from_sync_caller() -> None:
    judge = subscription_judge(query_fn=_fake_query([_FakeResultMessage('{"score": 0.9, "reason": "ok"}')]))
    assert judge.generate("score this") == '{"score": 0.9, "reason": "ok"}'
    got = judge.generate("score this", schema=_S)
    assert isinstance(got, _S)
    assert got.score == 0.9


def test_generate_sync_from_inside_running_loop() -> None:
    """deepeval calls sync generate from async contexts too — must not die on 'already running loop'."""
    judge = subscription_judge(query_fn=_fake_query([_FakeResultMessage("loop-safe")]))

    async def _caller() -> str | BaseModel:
        return judge.generate("score this")  # sync call while this loop is running

    assert asyncio.run(_caller()) == "loop-safe"


# ---------------------------------------------------------------------------
# Subscription-only auth + identity
# ---------------------------------------------------------------------------


def test_anthropic_api_key_is_popped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing the judge pops ANTHROPIC_API_KEY — subscription CLI auth only, never the paid API."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-must-never-be-used")
    subscription_judge(query_fn=_fake_query([_FakeResultMessage("x")]))
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_get_model_name_and_factory() -> None:
    judge = subscription_judge()
    assert isinstance(judge, SubscriptionJudge)
    assert judge.get_model_name() == "claude-sonnet-4-6"
    assert judge.load_model() is judge
    custom = subscription_judge(model="claude-opus-4-6")
    assert custom.get_model_name() == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Fault surfacing — a broken judge must be VISIBLE, never a silent score
# ---------------------------------------------------------------------------


def test_sdk_fault_surfaces_as_judge_error_not_typeerror() -> None:
    """An SDK fault mid-stream raises SubscriptionJudgeError. Critically it must NOT leak a
    TypeError: deepeval's generate_with_schema swallows TypeError as 'provider rejects the
    schema kwarg' and silently retries without the schema."""

    async def _boom(prompt: str) -> AsyncIterator[Any]:
        yield _FakeSystemMessage()
        raise TypeError("sdk internal fault")

    judge = subscription_judge(query_fn=_boom)
    with pytest.raises(SubscriptionJudgeError):
        asyncio.run(judge.a_generate("score this"))


def test_empty_stream_is_an_error() -> None:
    """A stream with no text-bearing message is a judge fault, not an empty verdict."""
    judge = subscription_judge(query_fn=_fake_query([_FakeSystemMessage()]))
    with pytest.raises(SubscriptionJudgeError):
        asyncio.run(judge.a_generate("score this"))
