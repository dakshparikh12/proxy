"""Drive the REAL orchestrator wake turn on the REAL provider path.

Two drivers, both over the REAL :class:`harness.wake_turn.WakeTurn` and the REAL
:class:`harness.provider.ClaudeAgentProvider` (the single Claude Agent SDK call
site) — no fakes below the seam:

* :func:`drive_product_wake` builds the ``WakeTurn`` EXACTLY as the live path does
  (``live_brain.build_wake_turn`` → provider + ``behaviors.REGISTRY``), with NO
  code-intel server mounted — because the product never mounts one. This is the
  faithful product path; it reveals whether Proxy, as assembled today, can answer
  a grounded question.

* :func:`drive_wired_wake` mounts the REAL code_intel SDK MCP server through the
  SAME public seam the product *should* use (``BehaviorRunner(..., mcp_servers=…)``
  — the constructor param already exists) and swaps in a behavior whose
  ``allowed_tools`` are the MCP-namespaced ``mcp__code_intel__*`` names. This
  measures whether the brain CAN answer grounded WHEN the seam is wired — isolating
  "can it answer?" from "is it plugged in?". It edits NO product code — it composes
  the existing public primitives (the runner's ``mcp_servers`` param, an
  ``agentkit.Behavior``) the way a correct product wiring would.

Both collect the streamed ``AgentChunk``s into a captured turn: the delivered
answer (the ``speak``/``send_chat`` delivery-tool text, per CANONICAL §12.3 — the
model's bare TEXT is reasoning, not delivery), every tool call, and a transcript.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from agentkit import Behavior, BehaviorConfig, BehaviorRunner
from harness import behaviors
from harness.provider import ClaudeAgentProvider
from harness.wake_turn import StateDigest, WakeEvent, WakeTurn

from ._fixture_repo import CODE_INTEL_TOOLS, make_code_intel_sdk_server

# The delivery verbs a wake turn speaks/types through (CANONICAL §12.3). The model's
# answer to the user is whatever it hands these tools; bare TEXT is reasoning.
_DELIVERY_TOOLS = {"speak", "send_chat", "show_screen"}


@dataclass
class CapturedTurn:
    answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    tool_inputs: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)
    transcript_lines: list[str] = field(default_factory=list)
    error: str | None = None
    #: Scratch space a scenario uses to attach its retrieval context before scoring.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def transcript(self) -> str:
        return "\n".join(self.transcript_lines)


async def _collect(stream: Any) -> CapturedTurn:
    turn = CapturedTurn()
    delivery_texts: list[str] = []
    try:
        async for chunk in stream:
            ctype = getattr(chunk, "type", "")
            meta = getattr(chunk, "metadata", {}) or {}
            text = getattr(chunk, "text", "") or ""
            if ctype == "TEXT":
                turn.text_chunks.append(text)
                turn.transcript_lines.append(f"TEXT: {text}")
            elif ctype == "TOOL_USE":
                name = str(meta.get("name", ""))
                tin = dict(meta.get("input", {}) or {})
                turn.tool_calls.append(name)
                turn.tool_inputs.append(tin)
                turn.transcript_lines.append(f"TOOL_USE: {name} input={json.dumps(tin)[:400]}")
                # The model's answer to the human is what it hands a delivery verb.
                if name in _DELIVERY_TOOLS:
                    spoken = tin.get("text") or tin.get("chunk") or tin.get("patch") or ""
                    if spoken:
                        delivery_texts.append(str(spoken))
            elif ctype == "TOOL_RESULT":
                turn.tool_results.append(text)
                turn.transcript_lines.append(f"TOOL_RESULT: {text[:400]}")
            elif ctype == "INIT":
                turn.transcript_lines.append(
                    f"INIT: session={meta.get('session_id','')} "
                    f"mcp_servers={meta.get('mcp_servers', [])} tools={meta.get('tools', [])}"
                )
            elif ctype == "RESULT":
                turn.transcript_lines.append(
                    f"RESULT: cost={meta.get('total_cost_usd')} turns={meta.get('num_turns')} "
                    f"text={text[:400]!r}"
                )
                # ResultMessage.result is the model's final text — a fallback answer if
                # the model spoke plain text without a delivery verb (some turns do).
                if text and not delivery_texts:
                    delivery_texts.append(text)
            elif ctype == "ERROR":
                turn.error = str(meta.get("message", ""))
                turn.transcript_lines.append(f"ERROR: {turn.error}")
    except Exception as exc:  # noqa: BLE001 - surface a provider blow-up as a captured error
        turn.error = f"{type(exc).__name__}: {exc}"
        turn.transcript_lines.append(f"EXCEPTION: {turn.error}")

    # The delivered answer: the delivery-verb text if any, else the accumulated TEXT
    # (so a turn that reasons out loud without a delivery verb is still scored on its
    # actual content — an honest read of what Proxy produced).
    if delivery_texts:
        turn.answer = "\n".join(delivery_texts)
    elif turn.text_chunks:
        # TEXT chunks are ACCUMULATED per msg_id by the seam; the last is the fullest.
        turn.answer = turn.text_chunks[-1]
    return turn


def _digest() -> StateDigest:
    return StateDigest(
        tasks_in_flight=("indexing acme/webapp (done)",),
        mouth_busy=False,
        component_health="all green",
    )


def drive_product_wake(question: str, *, speaker: str = "dev", max_turns: int = 4) -> CapturedTurn:
    """Run the wake turn EXACTLY as the product assembles it (no code_intel mount)."""
    provider = ClaudeAgentProvider(sandbox_mode=False)
    turn = WakeTurn(
        meeting_id="cap-eval-product",
        provider=provider,
        registry=behaviors.REGISTRY,
        digest=_digest(),
    )
    event = WakeEvent(text=question, speaker=speaker)
    return asyncio.run(_collect(turn.wake(event, behavior="answer-question")))


def _wired_answer_behavior() -> Behavior:
    """An answer behavior whose tools are the MCP-namespaced code_intel names.

    Structurally identical to the product ``answer-question`` behavior (same role /
    ANSWER seat), but its ``allowed_tools`` name the MOUNTED ``mcp__code_intel__*``
    tools + the delivery verbs — the tool names a CORRECT product wiring would
    advertise (the product's bare ``get_dependents`` names don't resolve to any
    mounted server). No product file is edited; this is the wiring the seam should
    carry, expressed through the public ``Behavior`` primitive.
    """
    role = (
        "You are Proxy, the AI agent on this engineering call. Someone addressed a "
        "question to you. You have code-intelligence tools over the team's real "
        "codebase graph: mcp__code_intel__who_calls, mcp__code_intel__get_dependents, "
        "mcp__code_intel__find_references, mcp__code_intel__who_writes, and "
        "mcp__code_intel__read. Use them to answer grounded, citing the real file:line. "
        "If the answer is a simple grounded lookup, call the right tool, then speak a "
        "short cited answer via the speak tool. If the question is ambiguous, ask one "
        "clarifying question. If the symbol is not in this codebase, say 'not found by "
        "this method' — never invent a location. Speak short: two sentences max."
    )
    rules = (
        "who calls X -> mcp__code_intel__who_calls; where is X used -> find_references; "
        "who writes the T table -> who_writes; where is X defined -> read the file.",
        "Never invent a file:line. Ground every citation in a tool result. If nothing "
        "resolves, say so plainly.",
        "Deliver your final answer to the human by calling the speak tool with the text.",
    )
    tools = (*CODE_INTEL_TOOLS, "speak", "send_chat")
    return Behavior(
        name="answer-question",
        role=role,
        rules=rules,
        inputs=("event", "state_digest", "notes_ref"),
        config=BehaviorConfig(
            name="answer-question",
            model="claude-sonnet-4-6",
            max_turns=6,
            role="answer-question",
            rules=rules,
            inputs=("event", "state_digest", "notes_ref"),
            tools=tools,
        ),
    )


def drive_wired_wake(
    question: str,
    *,
    server: Any,
    speaker: str = "dev",
    tool_log: list[str] | None = None,
) -> CapturedTurn:
    """Run the wake turn WITH the real code_intel SDK MCP server mounted via the seam.

    Builds the real ``WakeTurn`` + real ``ClaudeAgentProvider`` (sandbox OFF — this
    host-side code_intel read is not an E2B tool), then wires the code_intel server
    through the runner's PUBLIC ``mcp_servers`` param (the same param the Workroom
    uses for its ``code`` / ``propose_change`` servers). No product code is edited.
    """
    sdk_server = make_code_intel_sdk_server(server, tool_log=tool_log)
    behavior = _wired_answer_behavior()
    registry = {"answer-question": behavior}
    provider = ClaudeAgentProvider(sandbox_mode=False)
    turn = WakeTurn(
        meeting_id="cap-eval-wired",
        provider=provider,
        registry=registry,
        digest=_digest(),
    )
    # Wire the code_intel server through the SAME public BehaviorRunner seam the
    # product wiring should use. WakeTurn builds its runner without mcp_servers
    # (the product defect); we rebuild the runner WITH the server so the mounted
    # code_intel tools are reachable — proving the brain can answer when wired.
    turn._runner = BehaviorRunner(  # noqa: SLF001 - test wires the public mcp_servers seam
        registry=registry,
        provider=provider,
        mcp_servers={"code_intel": sdk_server},
    )
    event = WakeEvent(text=question, speaker=speaker)
    return asyncio.run(_collect(turn.wake(event, behavior="answer-question")))
