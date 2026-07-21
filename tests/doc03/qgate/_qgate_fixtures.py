"""Shared, deterministic fixtures for the doc03 quality-gate unit tests.

No network, no vendor SDK. The fake ``call_external`` seam HONOURS the real seam
contract (runs the op once, wraps in an Outcome) — it never replaces request
construction or the parse path, and it is NOT a Mock() of the client faking a pass.
The gate response bodies here are the cassette-body stand-ins a recorded response
would supply; the real vendor round-trip is exercised only at the (skipped) reality
tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from scribe.schema import (
    AddOp,
    Claim,
    ContextLine,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
    PatchOp,
    Provenance,
    Reversibility,
)


# -- schema entry builders --------------------------------------------------

def a_context(text: str = "small talk") -> ContextLine:
    return ContextLine(text=text)


def a_decision_final(text: str = "ship the checkout refactor") -> Decision:
    return Decision(text=text, status=DecisionStatus.final, reversibility=Reversibility.easy)


def a_decision_forming(text: str = "maybe ship it") -> Decision:
    return Decision(text=text, status=DecisionStatus.forming, reversibility=Reversibility.easy)


def an_irreversible_decision(text: str = "delete the prod database") -> Decision:
    return Decision(
        text=text, status=DecisionStatus.forming, reversibility=Reversibility.irreversible
    )


def a_reversible_decision(text: str = "rename a local var") -> Decision:
    return Decision(
        text=text, status=DecisionStatus.forming, reversibility=Reversibility.easy
    )


def a_contradicting_claim(text: str = "we are NOT shipping today") -> Claim:
    return Claim(
        text=text,
        speaker="Ana",
        said_at_s=12.0,
        firmness=Firmness.firm,
        provenance=Provenance.observed,
        contradicts="e1",
    )


def a_plain_claim(text: str = "the build is green") -> Claim:
    return Claim(
        text=text,
        speaker="Zed",
        said_at_s=3.0,
        firmness=Firmness.firm,
        provenance=Provenance.observed,
    )


# -- fake vendor response blocks (cassette-body stand-ins) ------------------

@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    input: dict
    type: str = "tool_use"
    name: str = "emit_notes_delta"


@dataclass
class FakeResp:
    content: list
    stop_reason: str = "tool_use"


def grounded_true_resp(reason: str = "the note follows from the window") -> FakeResp:
    return FakeResp(
        content=[TextBlock(text='{"grounded": true, "reason": "%s"}' % reason)],
        stop_reason="end_turn",
    )


def grounded_false_resp(reason: str = "the window does not support this note") -> FakeResp:
    return FakeResp(
        content=[TextBlock(text='{"grounded": false, "reason": "%s"}' % reason)],
        stop_reason="end_turn",
    )


def unparseable_resp() -> FakeResp:
    return FakeResp(content=[TextBlock(text="I cannot answer that.")], stop_reason="end_turn")


def a_reextraction_resp(text: str = "corrected: we are shipping on Friday, not today") -> FakeResp:
    """A Sonnet re-extraction that emits one add op (a corrected context line)."""
    delta_input = {
        "ops": [{"op": "add", "entry": {"kind": "context", "text": text}}],
        "current_goal": None,
    }
    return FakeResp(content=[ToolUseBlock(input=delta_input)], stop_reason="tool_use")


def an_empty_reextraction_resp() -> FakeResp:
    """A Sonnet re-extraction that produced no extractable entry (empty ops)."""
    return FakeResp(
        content=[ToolUseBlock(input={"ops": [], "current_goal": None})],
        stop_reason="tool_use",
    )


# -- fake client + seam (contract-honouring, NOT a pass-faking Mock) --------

class RecordingMessages:
    """Captures messages.create kwargs and returns a queued response."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no more queued gate responses")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.messages = RecordingMessages(list(responses))


@dataclass
class Outcome:
    value: Any
    attempts: int = 1
    total_cost_usd: float = 0.0


def make_call_external(record: list | None = None) -> Callable[..., Any]:
    """A fake seam honouring the contract: run the op ONCE, wrap in an Outcome.

    Records the (service, model) of every wrapped call so tests can assert exactly
    which model each outbound request used — the real request construction path
    still runs; only the recorded HTTP body is supplied by the fake client.
    """

    async def _seam(op: Callable[[], Any], *, service: str, unit_cost_usd: float = 0.0) -> Outcome:
        value = await op()
        if record is not None:
            record.append(service)
        return Outcome(value=value)

    return _seam


@dataclass
class CapturingApplier:
    """The normal applier surface — records every PatchOp + its attribution.

    Stands in for the Scribe's applier seam. It applies the patch against a tiny
    note store that supersedes-not-erases (so the original entry is preserved), and
    records the attribution so a test can assert the correction was authored by the
    gate, went through a PatchOp, and left the original superseded-not-erased.
    """

    store: dict = field(default_factory=dict)
    patches: list[PatchOp] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    histories: dict = field(default_factory=dict)

    def seed(self, entry_id: str, current: dict) -> None:
        self.store[entry_id] = dict(current)
        self.histories[entry_id] = []

    def __call__(self, patch: PatchOp, *, attributed_to: str) -> None:
        self.patches.append(patch)
        self.attributions.append(attributed_to)
        if patch.target_id in self.store:
            # superseded-not-erased: snapshot the old value before mutating.
            self.histories.setdefault(patch.target_id, []).append(
                dict(self.store[patch.target_id])
            )
            self.store[patch.target_id] = {**self.store[patch.target_id], **patch.changes}


class FakeReExtractor:
    """A Sonnet re-extractor that returns a queued NoteDelta (or None) and counts calls."""

    def __init__(self, *deltas: Any) -> None:
        self._deltas = list(deltas)
        self.windows_seen: list[str] = []

    async def __call__(self, window_text: str) -> Any:
        self.windows_seen.append(window_text)
        if not self._deltas:
            return None
        return self._deltas.pop(0)


def a_delta_with_one_add(text: str = "corrected note") -> NoteDelta:
    return NoteDelta(ops=[AddOp(entry=ContextLine(text=text))])


def an_empty_delta() -> NoteDelta:
    return NoteDelta(ops=[])
