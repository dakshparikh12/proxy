"""The forced-output tool — the schema, forced.

The Scribe is invoked with exactly one tool, ``emit_notes_delta``, and
``tool_choice`` set to force it. The tool is not a capability — it is an output
*shape*: its ``input_schema`` is the JSON Schema derived from the ``NoteDelta``
Pydantic model, so the model must satisfy the same contract the runtime then
re-validates against. There is no free-text path and no execution tool.
"""
from __future__ import annotations

from typing import Any

from .schema import NoteDelta

# The single tool the Scribe may call. Its input_schema IS the NoteDelta contract:
# Pydantic is the source of truth for both the tool's JSON Schema and the runtime
# validator (belt-and-suspenders).
NOTE_DELTA_TOOL: dict[str, Any] = {
    "name": "emit_notes_delta",
    "description": (
        "Emit the structured note delta for this transcript window. Add new entries, "
        "patch existing ones by id. Never restate an unchanged fact — patch in place."
    ),
    "input_schema": NoteDelta.model_json_schema(),  # Pydantic IS the contract the model must satisfy
}

# The complete tool registry for the Scribe API call — exactly one tool, an output
# shape, no execution capability (no web-fetch, code-execution, or search tool).
SCRIBE_TOOLS: list[dict[str, Any]] = [NOTE_DELTA_TOOL]

# The forced tool_choice for every Scribe request: the model MUST call
# emit_notes_delta — never auto, never absent, never a different tool.
SCRIBE_TOOL_CHOICE: dict[str, str] = {"type": "tool", "name": "emit_notes_delta"}


def build_scribe_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    system: str | None = None,
) -> dict[str, Any]:
    """Assemble the Scribe request params — always tools + forced tool_choice.

    There is no code path that omits ``tool_choice`` or sets it to ``auto``: every
    request built here carries the single ``emit_notes_delta`` tool and forces it.
    """
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": SCRIBE_TOOLS,
        "tool_choice": SCRIBE_TOOL_CHOICE,
    }
    if system is not None:
        request["system"] = system
    return request


__all__ = [
    "NOTE_DELTA_TOOL",
    "SCRIBE_TOOLS",
    "SCRIBE_TOOL_CHOICE",
    "build_scribe_request",
]
