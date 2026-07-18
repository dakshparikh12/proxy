"""libs.agentkit.tools — the tool-handler registry + never-throw boundary.

§14 hard rule: *every tool handler returns errors, never throws*. Each handler
takes a single tool-input object and returns a :class:`HandlerResult`. Every
handler body is wrapped so that an internal fault (any exception raised while
reading the input or doing the work) is turned into an ERROR result rather than
propagated out of the handler boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HandlerResult:
    """A tool-handler outcome. ``is_error`` marks the never-throw error result."""

    is_error: bool
    payload: Any = None
    message: str = ""


def _error(message: str) -> HandlerResult:
    """Build the ERROR result a handler returns instead of throwing."""
    return HandlerResult(is_error=True, message=message)


def echo_handler(tool_input: Any) -> HandlerResult:
    """Echo the ``text`` item of the input; never throws under a faulty input."""
    try:
        text = tool_input["text"]
        return HandlerResult(is_error=False, payload=text)
    except Exception as exc:  # noqa: BLE001 - contract: return errors, never throw
        return _error(f"echo failed: {exc}")


def answer_handler(tool_input: Any) -> HandlerResult:
    """Answer the ``.question`` attribute of the input; never throws on fault."""
    try:
        question = tool_input.question
        return HandlerResult(is_error=False, payload=f"answered: {question}")
    except Exception as exc:  # noqa: BLE001 - contract: return errors, never throw
        return _error(f"answer failed: {exc}")


TOOL_HANDLERS: dict[str, Callable[[Any], HandlerResult]] = {
    "echo": echo_handler,
    "answer": answer_handler,
}
