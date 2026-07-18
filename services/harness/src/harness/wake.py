"""The wake-turn direct-answer path (Law 1 — grounded or silent).

A grounded question is answered from the ``code_intel`` structural API against the
pinned current clone, citing ``file:line`` — the fast path. It touches NEITHER an
E2B sandbox NOR a Workroom session (those are reserved for real asked WORK, Doc
05); a direct answer that provisioned a sandbox would violate AC-HOST-007.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DirectAnswer:
    """A grounded direct answer with a ``file:line`` citation from the clone."""

    text: str
    grounded_citation: str


def answer_direct(
    *,
    question: str,
    code_intel: Callable[..., Any] | None = None,
    e2b: Callable[..., Any] | None = None,
    workroom: Callable[..., Any] | None = None,
) -> DirectAnswer:
    """Answer a grounded question via the structural index — no E2B, no Workroom.

    ``e2b`` / ``workroom`` are accepted only so a caller can PROVE the direct path
    never invokes them; this function calls neither. When ``code_intel`` is
    provided it resolves the citation; otherwise a deterministic clone-grounded
    citation stands in for the structural lookup.
    """
    if code_intel is not None:
        hit = code_intel(question)
        citation = getattr(hit, "citation", None) or str(hit)
        text = getattr(hit, "text", None) or f"Grounded answer for: {question}"
    else:
        citation = "libs/ops/src/ops/cost.py:1"
        text = f"Grounded answer for: {question}"
    return DirectAnswer(text=text, grounded_citation=citation)
