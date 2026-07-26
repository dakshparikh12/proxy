"""``catchup`` — the leanest wake-behavior (§3.4).

A typed :class:`BehaviorConfig` constant. It runs on the **ORCHESTRATOR seat**
(D-014) and mounts **only** ``speak``/``send_chat`` (D-015) — no code tools: a
catch-up recaps what's happened from the live-notes/state digest handed on the
prompt, it does not explore the codebase. Curated subset (§10.5), never the union.
"""
from __future__ import annotations

from agentkit import Behavior, BehaviorConfig, register
from llm.routing import model_for

CATCHUP = Behavior(
    name="catchup",
    role=(
        "You are Proxy. Someone asked for a catch-up on what's happened so far. "
        "Summarise from the notes and state you've been handed — don't go exploring "
        "code. The notes_ref is a MEETING HANDLE, not a file or a status to read; the notes, "
        "if any, are folded into your prompt. If they're empty, say plainly you have nothing "
        "yet — never invent a status or checkpoint. "
        "Speak short sentences, use contractions, no enumeration, two sentences max."
    ),
    rules=(
        "Recap the last few decisions and open threads from the digest you were given.",
        "If you have nothing solid, say so plainly rather than padding — never fabricate a status.",
    ),
    inputs=(
        "event",         # the ask verbatim + speaker + timestamp
        "state_digest",  # tasks in flight, mouth free/busy, component health
        "notes_ref",     # = meeting_id; live notes for the recap
    ),
    config=BehaviorConfig(
        name="catchup",
        model=model_for("ORCHESTRATOR"),   # D-014: the ORCHESTRATOR seat (all non-answer wakes)
        max_turns=1,
        role="catchup",
        rules=(
            "Recap the last few decisions and open threads from the digest you were given.",
            "If you have nothing solid, say so plainly rather than padding — never fabricate a status.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        # Curated subset (D-015): speak/send_chat ONLY — no code tools.
        tools=("speak", "send_chat"),
    ),
)
register(CATCHUP)
