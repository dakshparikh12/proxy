"""``surface-risk`` — the blast-radius wake-behavior (§3.4).

A typed :class:`BehaviorConfig` constant on the **ORCHESTRATOR seat** (D-014). It
mounts the read-only structural tools ``grep``/``read``/``get_dependents`` plus
``speak`` (D-015) so Proxy can look up the blast radius of a change and voice the
risk with a cited ``file:line`` — but it cannot dispatch or write anything.
Curated subset (§10.5), never the union.
"""
from __future__ import annotations

from agentkit import Behavior, BehaviorConfig, register
from llm.routing import model_for

SURFACE_RISK = Behavior(
    name="surface-risk",
    role=(
        "You are Proxy. Something in this discussion looks risky — a change with a "
        "wide blast radius, a decision that contradicts the code. Check the dependents "
        "and the relevant files, then say the risk plainly with the cited file:line. "
        "Speak short sentences, use contractions, no enumeration, two sentences max."
    ),
    rules=(
        "A change to a widely-depended-on symbol usually warrants naming who breaks.",
        "Ground every risk you raise in a file and line; if you can't, stay silent.",
    ),
    inputs=(
        "event",         # the utterance that triggered the risk check
        "state_digest",  # tasks in flight, component health
        "notes_ref",     # = meeting_id; live notes for context
    ),
    config=BehaviorConfig(
        name="surface-risk",
        model=model_for("ORCHESTRATOR"),   # D-014: the ORCHESTRATOR seat
        max_turns=3,
        role="surface-risk",
        rules=(
            "A change to a widely-depended-on symbol usually warrants naming who breaks.",
            "Ground every risk you raise in a file and line; if you can't, stay silent.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        # Curated subset (D-015): read-only structural tools + speak. No dispatch, no write.
        # The code-intel tools are MCP-namespaced ``mcp__code_intel__*`` so ``allowed_tools``
        # resolves to the MOUNTED code_intel SDK server (a bare name would name no mounted tool).
        # ``speak`` stays bare — it is a host-side SDK-local delivery verb, not an MCP tool.
        tools=(
            "mcp__code_intel__grep", "mcp__code_intel__read",
            "mcp__code_intel__get_dependents", "speak",
        ),
    ),
)
register(SURFACE_RISK)
