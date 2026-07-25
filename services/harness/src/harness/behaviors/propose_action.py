"""``propose-action`` — the dispatch-only wake-behavior (§3.4).

A typed :class:`BehaviorConfig` constant on the **ORCHESTRATOR seat** (D-014). It
mounts **only** ``dispatch_workroom`` (D-015): its whole job is to stage a build/
action into the Workroom as a draft — it delivers nothing itself and reads no code
(the Workroom does that). Curated subset (§10.5), never the union.
"""
from __future__ import annotations

from agentkit import Behavior, BehaviorConfig, register
from llm.routing import model_for

PROPOSE_ACTION = Behavior(
    name="propose-action",
    role=(
        "You are Proxy. There's a concrete piece of work worth doing — a change, a "
        "build, an investigation. Frame it and dispatch it to the workroom as a draft "
        "for the humans to approve. Keep the framing short; don't do the work here."
    ),
    rules=(
        "Turn the ask into one clear workroom brief; don't try to build it yourself.",
        "The dispatch is a staged draft behind a human click — never a done action.",
    ),
    inputs=(
        "event",         # the ask that prompted the proposal
        "state_digest",  # tasks in flight, component health
        "notes_ref",     # = meeting_id; live notes for context
    ),
    config=BehaviorConfig(
        name="propose-action",
        model=model_for("ORCHESTRATOR"),   # D-014: the ORCHESTRATOR seat
        max_turns=2,
        role="propose-action",
        rules=(
            "Turn the ask into one clear workroom brief; don't try to build it yourself.",
            "The dispatch is a staged draft behind a human click — never a done action.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        # Curated subset (D-015): dispatch_workroom ONLY.
        tools=("dispatch_workroom",),
    ),
)
register(PROPOSE_ACTION)
