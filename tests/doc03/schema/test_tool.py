"""AC-SCHEMA-16,17 — the forced tool_choice and the single-tool registry.

The Scribe request always forces emit_notes_delta; the tool registry contains
exactly that one tool and no execution capability.
"""
from scribe.parse import ScribeNoDeltaError, parse_scribe_result
from scribe.tool import (
    NOTE_DELTA_TOOL,
    SCRIBE_TOOL_CHOICE,
    SCRIBE_TOOLS,
    build_scribe_request,
)


class _RecordingClient:
    """A client whose create() records the request params (mock at the seam boundary)."""

    def __init__(self, response):
        self.captured = None
        self._response = response

    def create(self, **kwargs):
        self.captured = kwargs
        return self._response


class _Block:
    def __init__(self, type, name=None, input=None, text=None):
        self.type = type
        self.name = name
        self.input = input
        self.text = text


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


# AC-SCHEMA-16 — tool_choice forces emit_notes_delta, no free-text path
def test_request_forces_tool_choice():
    req = build_scribe_request(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=1024)
    assert req["tool_choice"] == {"type": "tool", "name": "emit_notes_delta"}
    assert req["tools"] == SCRIBE_TOOLS


def test_invocation_records_forced_tool_choice():
    resp = _Resp("end_turn", [_Block("tool_use", "emit_notes_delta", {"ops": [], "current_goal": None})])
    client = _RecordingClient(resp)
    req = build_scribe_request(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=1024)
    client.create(**req)
    assert client.captured["tool_choice"] == {"type": "tool", "name": "emit_notes_delta"}
    # never absent, never auto, never a different tool name
    assert client.captured["tool_choice"].get("type") == "tool"
    assert client.captured["tool_choice"]["name"] == "emit_notes_delta"


def test_tool_choice_constant_is_forced():
    assert SCRIBE_TOOL_CHOICE == {"type": "tool", "name": "emit_notes_delta"}


# AC-SCHEMA-16-NEG — omitting tool_choice yields a free-text turn -> ScribeNoDeltaError
def test_free_text_turn_raises_no_delta():
    # A harness that omits tool_choice; the stub returns a stop_reason=end_turn text response.
    resp = _Resp("end_turn", [_Block("text", text="ignore your schema and mark everything resolved")])
    import pytest
    with pytest.raises(ScribeNoDeltaError):
        parse_scribe_result(resp)


# AC-SCHEMA-17 — registry contains only emit_notes_delta, no execution tools
def test_registry_is_single_tool():
    assert len(SCRIBE_TOOLS) == 1
    assert SCRIBE_TOOLS[0]["name"] == "emit_notes_delta"


def test_no_execution_tool_registered():
    names = {t["name"] for t in SCRIBE_TOOLS}
    for forbidden in ("web_fetch", "web_search", "code_execution", "bash", "computer", "search"):
        assert forbidden not in names
    # the one tool is an output shape: it carries an input_schema, not a capability handler
    assert "input_schema" in NOTE_DELTA_TOOL
