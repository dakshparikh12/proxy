"""``answer-question`` — the normative wake-behavior (AMENDMENT C3, §3.4).

A typed :class:`BehaviorConfig` constant (NO YAML — CANONICAL §12.5). It runs on
the **ANSWER seat** (D-014) and mounts the ``code_intel`` read tools
(``get_dependents``/``who_writes``/``list_entry_points``/``grep``/``read``/
``batch_read``) PLUS the orchestration delivery verbs so Proxy can **DIRECT-ANSWER**
a simple grounded lookup with a cited ``file:line`` — not only dispatch to the
Workroom (§3.4, D-015). The tool list is the curated subset (§10.5), never the union.

The ``role`` + ``rules`` prime model judgment; they are examples, never an
``if-X-do-Y`` decision table (D-023). The spoken-register line lives here (prompt
location 1); ``with_proxy_guardrails`` supplies it again (location 2).
"""
from __future__ import annotations

from agentkit import Behavior, BehaviorConfig, register
from llm.routing import model_for

# The code_intel read tools this behavior mounts so it can answer a grounded lookup
# itself (host-side code_intel API, §12.2), plus the delivery + dispatch verbs
# (D-024). This is the curated subset (§10.5, D-015) — NOT the union of Proxy's tools.
ANSWER_QUESTION = Behavior(
    name="answer-question",
    role=(
        "You are Proxy, the agent on this call. A question was addressed to you. "
        "Decide the path: a simple grounded code lookup you ANSWER DIRECTLY in this "
        "turn using the mounted code_intel tools and speak the cited file:line; real "
        "work you dispatch to the workroom and present when it returns. "
        "Speak short sentences, use contractions, no enumeration, two sentences max."
    ),
    rules=(
        # Examples that prime judgment — NOT a decision table (D-023):
        "A simple grounded lookup usually deserves a direct code_intel answer spoken "
        "at the next boundary with the cited file and line.",
        "A large build usually deserves a detached workroom dispatch with an "
        "async-etiquette line. Decide per case; nothing here forces a tool.",
        "Never invent an answer. If the workroom needs a clarification, relay its one "
        "question through your mouth like any result.",
    ),
    inputs=(
        "event",         # the wake payload: the ask verbatim + speaker + timestamp
        "state_digest",  # tasks in flight, mouth free/busy, component health
        "notes_ref",     # = meeting_id; live notes read via GET /internal/notes/{meeting_id}
    ),
    config=BehaviorConfig(
        name="answer-question",
        model=model_for("ANSWER"),   # D-014: the ANSWER seat (grounded-answer tier)
        max_turns=4,
        role="answer-question",
        rules=(
            "A simple grounded lookup usually deserves a direct code_intel answer spoken "
            "at the next boundary with the cited file and line.",
            "A large build usually deserves a detached workroom dispatch with an "
            "async-etiquette line. Decide per case; nothing here forces a tool.",
            "Never invent an answer. If the workroom needs a clarification, relay its one "
            "question through your mouth like any result.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        # DIRECT-ANSWER + dispatch envelope (D-015): the code_intel read tools so it can
        # answer itself, PLUS the orchestration verbs. Curated subset, never the union.
        tools=(
            "get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read",
            "dispatch_workroom", "speak", "send_chat", "show_screen", "ack", "cancel_task",
        ),
    ),
)
register(ANSWER_QUESTION)
