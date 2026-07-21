"""Shared, deterministic fixtures for the doc03 Scribe unit tests.

No network, no vendor SDK. A minimal duck-typed window + a fake Anthropic client
and a fake ``call_external`` seam that honour the real seam CONTRACT (they do NOT
replace request construction or the parse path — they only stand in for the
recorded HTTP body a cassette would provide).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scribe.prefix import MeetingHeader


@dataclass(frozen=True)
class FakeSeg:
    speaker: str
    text: str
    start_s: float = 0.0
    end_s: float = 1.0
    token_count: int = 5


@dataclass(frozen=True)
class FakeChat:
    sender: str
    text: str
    ts_s: float = 0.5


@dataclass(frozen=True)
class FakeWindow:
    segments: tuple[FakeSeg, ...]
    chat_messages: tuple[FakeChat, ...] = ()


def a_meeting() -> MeetingHeader:
    return MeetingHeader(
        meeting_id="m1",
        agenda="Ship the checkout refactor",
        participants=("Zed", "Ana", "Mel"),
        glossary={"retry logic": "the backoff loop", "checkout": "the payment flow"},
    )


def a_window(text: str = "We should ship the retry logic today.", speaker: str = "Ana") -> FakeWindow:
    return FakeWindow(segments=(FakeSeg(speaker=speaker, text=text),))


@dataclass
class ToolUseBlock:
    input: dict[str, Any]
    type: str = "tool_use"
    name: str = "emit_notes_delta"


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResp:
    content: list[Any]
    stop_reason: str = "tool_use"
    usage: Any = None


class RecordingMessages:
    """Captures messages.create kwargs and returns a canned response (cassette body stand-in)."""

    def __init__(self, resp: Any) -> None:
        self.resp = resp
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.resp


class FakeClient:
    def __init__(self, resp: Any) -> None:
        self.messages = RecordingMessages(resp)


@dataclass
class Outcome:
    value: Any
    attempts: int = 1
    total_cost_usd: float = 0.0


def make_call_external(record: list[str] | None = None) -> Callable[..., Any]:
    """A fake seam HONOURING the contract: run the op once, wrap in an Outcome."""

    async def _seam(op: Callable[[], Any], *, service: str, unit_cost_usd: float = 0.0) -> Outcome:
        if record is not None:
            record.append(service)
        value = await op()
        return Outcome(value=value)

    return _seam


def a_valid_delta_input() -> dict[str, Any]:
    return {"ops": [{"op": "add", "entry": {"kind": "context", "text": "small talk"}}], "current_goal": None}
