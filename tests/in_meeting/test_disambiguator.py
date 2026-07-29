"""Acceptance battery for the DISAMBIGUATOR node — ``in_meeting.disambiguator``.

The trigger's voice path fires on a mechanical ``\\bproxy\\b`` hit; then ONE tiny
bounded model call resolves "addressed to me, or the common noun ('proxy
server')?" (SPEC §2/§3.1 — pennies, only on hits). This battery is OFFLINE: the
SDK seam is the injectable ``query_fn`` (the proven ``tests/eval/
subscription_judge.py`` pattern), so what is proven deterministically is the
parse, the prompt embedding, the fail-open policy, the subscription option
triad, and the async trigger integration. The live subscription smoke
("the proxy server timed out" → NO / "Proxy, what's the retry logic?" → YES)
is the controller's, not this file's.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from in_meeting.disambiguator import (
    DEFAULT_DISAMBIGUATOR_MODEL,
    Disambiguator,
    build_disambiguator,
)
from in_meeting.notes import TranscriptLine
from in_meeting.trigger import EngagementTrigger

# ---------------------------------------------------------------------------
# Fake SDK-shaped messages (duck-typed like claude_agent_sdk.types — the same
# shapes tests/eval/test_subscription_judge.py drives the judge with)
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
    """SystemMessage/RateLimitEvent stand-in: no text-bearing attrs — ignored."""


def _fake_query(
    messages: list[Any], prompts: list[str] | None = None
) -> Callable[[str], AsyncIterator[Any]]:
    """Build an injectable fake ``query_fn`` yielding the given message sequence.

    ``prompts`` (when given) captures every prompt the disambiguator sends —
    the seam for the embed-the-line-verbatim assertion.
    """

    async def _q(prompt: str) -> AsyncIterator[Any]:
        assert isinstance(prompt, str) and prompt
        if prompts is not None:
            prompts.append(prompt)
        for message in messages:
            yield message

    return _q


def _line(text: str, speaker: str = "Priya", t: float = 0.0) -> TranscriptLine:
    return TranscriptLine(text=text, speaker=speaker, timestamp=t, end_of_turn=True)


# ---------------------------------------------------------------------------
# AC1 — exact YES/NO parse, fenced/prose-tolerant, both SDK text shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_text", "expected"),
    [
        ("YES", True),
        ("YES.", True),
        ("yes", True),
        ("The answer is YES", True),
        ("```\nYES\n```", True),
        ("NO", False),
        ("NO.", False),
        ("no", False),
        ("The answer is NO", False),
        ("NO — that's a proxy server mention, not an address.", False),
    ],
)
async def test_yes_no_parse_from_result_message(model_text: str, expected: bool) -> None:
    """The FIRST YES/NO token in the collected text decides, case-insensitive."""
    disambiguate = build_disambiguator(query_fn=_fake_query([_FakeResultMessage(model_text)]))
    assert await disambiguate("Proxy, what's the retry logic?") is expected
    assert disambiguate.last_error is None, "a clean parse must leave no fault recorded"


@pytest.mark.asyncio
async def test_yes_no_parse_from_assistant_text_blocks() -> None:
    """No ResultMessage → the concatenated AssistantMessage text blocks decide."""
    yes = build_disambiguator(query_fn=_fake_query([_FakeAssistantMessage(["YES."])]))
    assert await yes("Proxy, check the deploy?") is True
    assert yes.last_error is None

    no = build_disambiguator(query_fn=_fake_query([_FakeAssistantMessage(["The answer is NO"])]))
    assert await no("The proxy server timed out.") is False
    assert no.last_error is None


@pytest.mark.asyncio
async def test_first_token_wins_over_a_later_one() -> None:
    """"NO. ... if yes were meant ..." parses as NO — the FIRST token, not any later one."""
    disambiguate = build_disambiguator(
        query_fn=_fake_query([_FakeResultMessage("NO. If YES were meant, the speaker would address it.")])
    )
    assert await disambiguate("We proxy the request through the edge gateway.") is False


# ---------------------------------------------------------------------------
# AC2 — the meeting line rides the prompt VERBATIM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_embeds_the_line_verbatim() -> None:
    line = "Proxy, what's the retry logic in billing-worker?"
    prompts: list[str] = []
    disambiguate = build_disambiguator(query_fn=_fake_query([_FakeResultMessage("YES")], prompts))

    assert await disambiguate(line) is True
    assert len(prompts) == 1, "exactly ONE bounded call per hit"
    assert line in prompts[0], "the spoken line must ride the prompt verbatim"


# ---------------------------------------------------------------------------
# AC3 — FAIL-OPEN: any SDK fault / unparseable output wakes, and the fault is
# visible on the callable (never a silent broken gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_on_a_raising_query_fn() -> None:
    async def _boom(prompt: str) -> AsyncIterator[Any]:
        raise RuntimeError("CLI transport died")
        yield  # pragma: no cover — makes this an async generator

    disambiguate = build_disambiguator(query_fn=_boom)
    assert await disambiguate("Proxy, are you there?") is True, "a broken confirm must WAKE"
    assert disambiguate.last_error is not None
    assert "CLI transport died" in disambiguate.last_error


@pytest.mark.asyncio
async def test_fail_open_on_an_empty_stream() -> None:
    disambiguate = build_disambiguator(query_fn=_fake_query([_FakeSystemMessage()]))
    assert await disambiguate("Proxy, are you there?") is True
    assert disambiguate.last_error is not None


@pytest.mark.asyncio
async def test_fail_open_on_garbled_output_without_a_yes_no_token() -> None:
    disambiguate = build_disambiguator(query_fn=_fake_query([_FakeResultMessage("Unclear — depends.")]))
    assert await disambiguate("Proxy, are you there?") is True
    assert disambiguate.last_error is not None


@pytest.mark.asyncio
async def test_last_error_reflects_the_LAST_call() -> None:
    """A fault is per-call state: the next clean call clears it (an honest gauge)."""
    outcomes: list[list[Any]] = [[], [_FakeResultMessage("NO")]]

    async def _q(prompt: str) -> AsyncIterator[Any]:
        for message in outcomes.pop(0):
            yield message

    disambiguate = build_disambiguator(query_fn=_q)
    assert await disambiguate("Proxy?") is True  # empty stream → fail-open
    assert disambiguate.last_error is not None
    assert await disambiguate("The proxy server is fine.") is False  # clean NO
    assert disambiguate.last_error is None


# ---------------------------------------------------------------------------
# AC4 — the real path's options carry the subscription triad + max_turns=1,
# and the paid API key is popped (subscription CLI auth only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_path_options_carry_the_triad_and_pop_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_agent_sdk

    captured: dict[str, Any] = {}

    def _capture_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        captured["prompt"] = prompt
        captured["options"] = options

        async def _stream() -> AsyncIterator[Any]:
            yield _FakeResultMessage("YES")

        return _stream()

    monkeypatch.setattr(claude_agent_sdk, "query", _capture_query)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-must-never-be-used")

    disambiguate = build_disambiguator()  # NO query_fn → the real (patched) SDK path
    assert await disambiguate("Proxy, check the deploy?") is True

    assert "ANTHROPIC_API_KEY" not in os.environ, "the key must be POPPED (subscription only)"
    options = captured["options"]
    assert options.model == DEFAULT_DISAMBIGUATOR_MODEL == "claude-haiku-4-5"
    assert options.max_turns == 1, "ONE bounded turn — no tool round-trips"
    assert options.permission_mode == "bypassPermissions"
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert "Proxy, check the deploy?" in captured["prompt"]


@pytest.mark.asyncio
async def test_model_override_reaches_the_options(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_agent_sdk

    captured: dict[str, Any] = {}

    def _capture_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        captured["options"] = options

        async def _stream() -> AsyncIterator[Any]:
            yield _FakeResultMessage("NO")

        return _stream()

    monkeypatch.setattr(claude_agent_sdk, "query", _capture_query)
    disambiguate = build_disambiguator(model="claude-sonnet-4-6")
    assert await disambiguate("the proxy server timed out") is False
    assert captured["options"].model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# AC5 — trigger integration: the async seam end-to-end (hit → await → wake /
# common-noun → no wake / idle → the confirm is NEVER touched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_awaits_the_async_confirm_only_on_hits() -> None:
    calls: list[str] = []
    labels = {
        "Proxy, what's the retry logic in billing-worker?": True,
        "The proxy server timed out again last night.": False,
    }

    async def confirm(text: str) -> bool:
        calls.append(text)
        return labels[text]  # KeyError = called on a non-hit line = a bug

    trig = EngagementTrigger(disambiguate=confirm)

    # Idle line: no wake, the confirm is never awaited.
    assert await trig.on_transcript(_line("Let's start with the incident review.")) is None
    assert calls == []

    # Common-noun hit: exactly one confirm call, no wake.
    assert await trig.on_transcript(_line("The proxy server timed out again last night.")) is None
    assert calls == ["The proxy server timed out again last night."]

    # Addressed: one more confirm call, voice wake carrying the ask verbatim.
    engagement = await trig.on_transcript(_line("Proxy, what's the retry logic in billing-worker?"))
    assert engagement is not None
    assert engagement.source == "voice"
    assert engagement.text == "Proxy, what's the retry logic in billing-worker?"
    assert calls[-1] == "Proxy, what's the retry logic in billing-worker?"


@pytest.mark.asyncio
async def test_trigger_wired_to_the_REAL_disambiguator_offline() -> None:
    """The real Disambiguator satisfies the trigger's async seam end-to-end."""
    reject: Disambiguator = build_disambiguator(query_fn=_fake_query([_FakeResultMessage("NO")]))
    trig = EngagementTrigger(disambiguate=reject)
    assert await trig.on_transcript(_line("The proxy server timed out again last night.")) is None

    confirm: Disambiguator = build_disambiguator(query_fn=_fake_query([_FakeResultMessage("YES")]))
    trig2 = EngagementTrigger(disambiguate=confirm)
    engagement = await trig2.on_transcript(_line("Proxy, what's the retry logic in billing-worker?"))
    assert engagement is not None
    assert engagement.source == "voice"
