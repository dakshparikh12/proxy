"""Conversational wake-behaviors — Doc 08 §2.4 features #1, #2, #5, #6, #10.

Node: ``orchestrator.conversational-behaviors`` (build-new). Spec ref: Doc 08 §2.4.

The "small features that make it feel finished" ride the SAME wake-behavior machinery
as every other wake (§3.4) — they are typed :class:`Behavior` constants (NO YAML,
CANONICAL §12.5), each registered with one ``register()`` line, each mounting a
CURATED tool subset (§10.5 / D-015). Selecting one by name IS the branch (D-023) — no
per-behavior code path exists here.

The five features realized as behaviors:

  #1 **catch-me-up** (:data:`CATCH_ME_UP`) — "Proxy, catch me up" → a ~20-second recap
     folded from the live notes object (what's been discussed, decided, open). Grounded
     in the notes handed on the prompt (Law 1), delivered through ``speak``/``send_chat``.
  #2 **where-are-we** (:data:`WHERE_ARE_WE`) — "where did we land?" → the current
     decisions + open questions, briefly. Same machinery, a different slice of the notes.
  #5 **dry-run** (:data:`DRY_RUN`) — "what *would* you do?" → the planned course of
     action, executing NOTHING. This behavior's whole reason to exist is the NEGATIVE
     contract: it mounts NO ``dispatch_workroom`` and NO sandbox/execute tool at all, so
     a plan can never silently become a build (Law 3 — every world-touching act is a
     staged draft behind a human click, and a plan is not that act).
  #6 **show-your-work** (:data:`SHOW_YOUR_WORK`) — "how did you get that?" → re-expand a
     PRIOR receipt (named in the ask) into its underlying reads/citations. It re-renders
     already-captured task telemetry; it never re-runs the work.
  #10 **capability-answer** (:data:`CAPABILITY_ANSWER`) — "what can you do?" → a crisp
     summary drawn from the capabilities catalog (:func:`capabilities_catalog`), which is
     DERIVED from Proxy's actual registered wake-behaviors — so the answer is grounded in
     the real toolbelt and cannot over-claim (§4.11 honesty rule).

Every one of these is a **bounded, delivery-only direct wake turn**: it grounds its
answer in already-produced substrate (notes, receipts, capabilities) and speaks + posts
the result. None dispatches a Workroom build; none provisions a sandbox. The forbidden
set is a STRUCTURAL guarantee (the tool subset), not a runtime hope.
"""
from __future__ import annotations

from agentkit import Behavior, BehaviorConfig, register
from llm.routing import model_for

# The delivery verbs every conversational behavior speaks + posts its answer with. This
# is the WHOLE curated subset for these behaviors (§10.5 / D-015): they answer from
# EXISTING substrate handed on the prompt, so they touch no code tools and — critically —
# no dispatch/execute tool. The absence of ``dispatch_workroom`` here IS the node's
# "dry-run touches nothing" contract, made structural.
_DELIVER: tuple[str, ...] = ("speak", "send_chat")

# The standing spoken-register line (prompt location 1; ``with_proxy_guardrails`` supplies
# location 2). Kept as a constant so every conversational role reads identically without a
# copy-paste drift.
_SPOKEN = (
    "Speak short sentences, use contractions, no enumeration, two sentences max."
)


# ── #1 catch-me-up ────────────────────────────────────────────────────────────
CATCH_ME_UP = Behavior(
    name="catch-me-up",
    role=(
        "You are Proxy. Someone asked you to catch them up on what's happened so far. "
        "Fold the live notes you've been handed into a ~20-second recap: what's been "
        "discussed, what's been decided, and what's still open. Ground it in the notes — "
        "don't go exploring the codebase and don't invent anything. The notes_ref you're "
        "given is a MEETING HANDLE, not a file or a status to read — the actual notes, if "
        "any, are folded into the prompt for you. If the notes you were handed are empty or "
        "missing, say plainly that there's nothing to catch up on yet — NEVER invent a "
        "status, a checkpoint, a decision, or progress that isn't in the notes. " + _SPOKEN
    ),
    rules=(
        "Recap the recent decisions and the open threads from the notes you were given.",
        "Keep it to roughly twenty seconds of speech; if there's little to report, say so plainly.",
        "If the notes handed to you are empty, say you have nothing yet — never fabricate a "
        "'checkpoint ready' or any status not grounded in the notes (Law 2).",
    ),
    inputs=(
        "event",         # the ask verbatim + speaker + timestamp
        "state_digest",  # tasks in flight, mouth free/busy, component health
        "notes_ref",     # = meeting_id; the live notes object folded into the recap
    ),
    config=BehaviorConfig(
        name="catch-me-up",
        model=model_for("ORCHESTRATOR"),   # D-014: the ORCHESTRATOR seat (non-answer wakes)
        max_turns=1,
        role="catch-me-up",
        rules=(
            "Recap the recent decisions and the open threads from the notes you were given.",
            "Keep it to roughly twenty seconds of speech; if there's little to report, say so plainly.",
            "If the notes handed to you are empty, say you have nothing yet — never fabricate a "
            "'checkpoint ready' or any status not grounded in the notes (Law 2).",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        tools=_DELIVER,   # deliver-only: speak + send_chat. No code tools, no dispatch.
    ),
)
register(CATCH_ME_UP)


# ── #2 where-are-we ───────────────────────────────────────────────────────────
WHERE_ARE_WE = Behavior(
    name="where-are-we",
    role=(
        "You are Proxy. Someone asked where the conversation landed. From the live notes "
        "you've been handed, state the current decisions and the open questions, briefly. "
        "Same source as a catch-up, a tighter slice — the state right now, grounded in the "
        "notes, nothing invented. The notes_ref is a MEETING HANDLE, not a file to read; the "
        "notes, if any, are folded into your prompt. If they're empty, say plainly nothing's "
        "been decided yet — never invent a status or a decision. " + _SPOKEN
    ),
    rules=(
        "Report the decisions that stand and the questions still open, from the notes you were given.",
        "If nothing has been decided yet, say that plainly rather than padding — never fabricate.",
    ),
    inputs=(
        "event",
        "state_digest",
        "notes_ref",     # = meeting_id; the live notes read for the current-state slice
    ),
    config=BehaviorConfig(
        name="where-are-we",
        model=model_for("ORCHESTRATOR"),
        max_turns=1,
        role="where-are-we",
        rules=(
            "Report the decisions that stand and the questions still open, from the notes you were given.",
            "If nothing has been decided yet, say that plainly rather than padding — never fabricate.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        tools=_DELIVER,
    ),
)
register(WHERE_ARE_WE)


# ── #5 dry-run — the negative contract: plan, execute NOTHING ─────────────────
DRY_RUN = Behavior(
    name="dry-run",
    role=(
        "You are Proxy. Someone asked what you WOULD do — not for you to do it. Describe "
        "the course of action you'd take: the steps, the files you'd touch, the checks "
        "you'd run. This is a plan spoken aloud, not work performed: you are not building "
        "anything and not staging a change here. " + _SPOKEN
    ),
    rules=(
        "Lay out the plan you'd follow — the shape of the work, not the work itself.",
        "This is a description of intent; a real change would be a separate, human-approved step.",
    ),
    inputs=(
        "event",         # the "what would you do?" ask
        "state_digest",
        "notes_ref",     # = meeting_id; context for the plan
    ),
    config=BehaviorConfig(
        name="dry-run",
        model=model_for("ORCHESTRATOR"),
        max_turns=2,
        role="dry-run",
        rules=(
            "Lay out the plan you'd follow — the shape of the work, not the work itself.",
            "This is a description of intent; a real change would be a separate, human-approved step.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        # Deliver-only: speak + send_chat. NO dispatch_workroom, NO sandbox tool — the
        # structural guarantee that a dry-run can never execute or dispatch (node contract).
        tools=_DELIVER,
    ),
)
register(DRY_RUN)


# ── #6 show-your-work — re-expand a prior receipt into its citations ──────────
SHOW_YOUR_WORK = Behavior(
    name="show-your-work",
    role=(
        "You are Proxy. Someone asked how you reached a prior answer. Expand the receipt "
        "for that answer — what you ran, what you read, the cited lines — from the "
        "telemetry you've been handed. You are re-rendering work already done, not "
        "re-running it. " + _SPOKEN
    ),
    rules=(
        "Walk back through the named receipt: the reads and the cited file:line behind the answer.",
        "If the receipt for that answer isn't in what you were handed, say so plainly.",
    ),
    inputs=(
        "event",         # the ask; names WHICH prior answer/receipt to expand
        "state_digest",
        "notes_ref",     # = meeting_id; context + the receipt telemetry to re-render
    ),
    config=BehaviorConfig(
        name="show-your-work",
        model=model_for("ORCHESTRATOR"),
        max_turns=1,
        role="show-your-work",
        rules=(
            "Walk back through the named receipt: the reads and the cited file:line behind the answer.",
            "If the receipt for that answer isn't in what you were handed, say so plainly.",
        ),
        inputs=("event", "state_digest", "notes_ref"),
        tools=_DELIVER,   # re-render the captured receipt; never re-run the work.
    ),
)
register(SHOW_YOUR_WORK)


# ── #10 capability-answer — grounded in the capabilities catalog ─────────────
CAPABILITY_ANSWER = Behavior(
    name="capability-answer",
    role=(
        "You are Proxy. Someone asked what you can do on this call. Summarise your actual "
        "abilities from the capabilities catalog you've been handed — ask you about the "
        "repo, have you build or analyse something, ask for a catch-up. Describe only what "
        "the catalog lists; never claim an ability that isn't there. " + _SPOKEN
    ),
    rules=(
        "Summarise the abilities in the catalog you were given, in plain language.",
        "Don't over-claim: if it isn't in the catalog, you can't do it — say so if asked.",
    ),
    inputs=(
        "event",         # the "what can you do?" ask
        "capabilities",  # the capabilities catalog (grounds the answer; never invented)
    ),
    config=BehaviorConfig(
        name="capability-answer",
        model=model_for("ORCHESTRATOR"),
        max_turns=1,
        role="capability-answer",
        rules=(
            "Summarise the abilities in the catalog you were given, in plain language.",
            "Don't over-claim: if it isn't in the catalog, you can't do it — say so if asked.",
        ),
        inputs=("event", "capabilities"),
        tools=_DELIVER,
    ),
)
register(CAPABILITY_ANSWER)


# ── the capabilities catalog — one source of truth, derived from the toolbelt ──

#: A short, user-facing label for each behavior Proxy exposes as a capability. This
#: is the ONLY UI-facing string the catalog ships — it never carries the internal
#: tool names or the model seat (naming discipline: user-visible strings carry no
#: internal component names). Keyed by the real registered behavior ``name`` so the
#: catalog can NEVER name a capability Proxy doesn't actually have.
_CAPABILITY_LABELS: dict[str, str] = {
    "answer-question": "answer a question about the codebase, grounded in a cited file and line",
    "surface-risk": "flag the blast radius of a change before you make it",
    "propose-action": "take on a build or a change and stage it for your approval",
    "catch-me-up": "catch you up on what's been discussed, decided, and left open",
    "where-are-we": "tell you where the conversation landed right now",
    "dry-run": "tell you what I'd do — the plan — without doing it",
    "show-your-work": "show my work: what I ran and read behind an answer",
    "capability-answer": "tell you what I can do on this call",
}


def capabilities_catalog() -> tuple[dict[str, str], ...]:
    """Build the "what can you do?" catalog from Proxy's REAL registered behaviors.

    The catalog is DERIVED from the live behaviors registry (the single source of
    truth for what Proxy can do, §4.7/§4.11) — one entry per registered behavior that
    carries a user-facing label. Because it reads the same registry the runner selects
    from, the capability-answer it grounds can never over-claim: an ability that isn't
    a registered behavior simply isn't in the catalog.

    Each entry is ``{"id": <behavior name>, "label": <user-facing label>}`` — no
    internal tool names, no model seat, nothing backend-only. Imported lazily to avoid
    an import cycle with the package ``__init__`` that assembles the registry.
    """
    from control_plane import behaviors as _bdir

    catalog: list[dict[str, str]] = []
    for name in _bdir.REGISTRY:
        label = _CAPABILITY_LABELS.get(name)
        if label:
            catalog.append({"id": name, "label": label})
    return tuple(catalog)


# The conversational wake-behaviors, in declaration order (Doc 08 §2.4 #1,2,5,6,10).
CONVERSATIONAL_BEHAVIORS: tuple[Behavior, ...] = (
    CATCH_ME_UP,
    WHERE_ARE_WE,
    DRY_RUN,
    SHOW_YOUR_WORK,
    CAPABILITY_ANSWER,
)


__all__ = [
    "CAPABILITY_ANSWER",
    "CATCH_ME_UP",
    "CONVERSATIONAL_BEHAVIORS",
    "DRY_RUN",
    "SHOW_YOUR_WORK",
    "WHERE_ARE_WE",
    "capabilities_catalog",
]
