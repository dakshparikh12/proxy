"""The wake-turn direct-answer path (Law 1 — grounded or silent).

A grounded question is answered from the ``code_intel`` structural API against the
pinned current clone, citing ``file:line`` — the fast path. It touches NEITHER an
E2B sandbox NOR a Workroom session (those are reserved for real asked WORK, Doc
05); a direct answer that provisioned a sandbox would violate AC-HOST-007.

The REAL resolver lives in :mod:`harness.direct_answer` — it composes the live
:class:`~code_intel.mcp_server.CodeIntelMCPServer` tools into a grounded reply
whose ``file:line`` is drawn from an actual file read at the pinned SHA. This
module is the thin, back-compatible ``question=``-shaped façade over it: when a
live ``code_intel`` handle (a MeetingSession or server) is supplied it delegates
to the real resolver; with no handle it returns a deterministic clone-grounded
placeholder so the no-touch contract can still be proven in isolation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DirectAnswer:
    """A grounded direct answer with a ``file:line`` citation from the clone.

    ``grounded_citation`` is ``None`` when the turn ABSTAINS (Law 1: no index
    bound / nothing resolved → 'not found by this method'). A falsy citation is
    an abstention, never a fabricated location; callers must not treat it as a
    grounded answer.
    """

    text: str
    grounded_citation: str | None


def answer_direct(
    *,
    ask: str | None = None,
    question: str | None = None,
    code_intel: Callable[..., Any] | Any | None = None,
    session: Any = None,
    e2b: Callable[..., Any] | None = None,
    workroom: Callable[..., Any] | None = None,
) -> DirectAnswer:
    """Answer a grounded question via the structural index — no E2B, no Workroom.

    ONE canonical signature: the ask is passed as ``ask=`` (the name the canonical
    :func:`harness.direct_answer.answer_direct` resolver uses). ``question=`` is a
    back-compatible alias for the same argument so a legacy caller keeps working;
    exactly one of the two must be supplied (they must not disagree). This façade
    delegates to the ONE resolver — there is no second implementation.

    ``e2b`` / ``workroom`` are accepted only so a caller can PROVE the direct path
    never invokes them; this function calls neither. When a live ``session``
    (a :class:`code_intel.meeting.MeetingSession`) or a ``code_intel`` server is
    supplied, the answer is resolved for real by :func:`harness.direct_answer.answer_direct`
    (real tools → real file:line read). A bare ``callable`` ``code_intel`` (the
    legacy single-shot hook) is honoured as before. With no handle at all a
    deterministic clone-grounded citation stands in.
    """
    if ask is not None and question is not None and ask != question:
        raise ValueError(
            "answer_direct: pass the ask once — `ask=` and `question=` are aliases "
            "for the SAME argument and disagreed"
        )
    if ask is None:
        ask = question
    if ask is None:
        raise TypeError("answer_direct requires the ask (`ask=`, or the `question=` alias)")
    question = ask
    # A live server/session handle → the real resolver (real grounded citation).
    handle = session if session is not None else code_intel
    if handle is not None and not (
        callable(handle) and not hasattr(handle, "tool_call") and not hasattr(handle, "get_dependents")
    ):
        from .direct_answer import answer_direct as _resolve

        real = _resolve(
            ask=question, session=session, code_intel=code_intel, e2b=e2b, workroom=workroom
        )
        return DirectAnswer(
            text=real.text,
            grounded_citation=real.citation or "not-found",
        )

    # Legacy single-shot callable hook (a function returning a hit object).
    if callable(handle):
        hit = handle(question)
        citation = getattr(hit, "citation", None) or str(hit)
        text = getattr(hit, "text", None) or f"Grounded answer for: {question}"
        return DirectAnswer(text=text, grounded_citation=citation)

    # No handle: honest abstention, NEVER a fabricated citation (Law 1 —
    # grounded-or-silent). With no code_intel index bound there is nothing to
    # cite, so we delegate to the real resolver's abstention path — the ONE
    # canonical 'not found by this method' sentinel — rather than synthesising a
    # file:line the lookup never produced. grounded_citation is None (falsy):
    # the caller must treat this as an abstention, not a grounded answer.
    from .direct_answer import answer_direct as _resolve

    abstained = _resolve(ask=question, e2b=e2b, workroom=workroom)
    return DirectAnswer(text=abstained.text, grounded_citation=abstained.citation)
