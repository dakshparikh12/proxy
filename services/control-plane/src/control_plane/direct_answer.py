"""Harness-side view of the wake-turn direct-answer resolver (Doc 00 · AC-HOST-007).

The ONE canonical resolver now lives in the ``code_intel`` layer
(:mod:`code_intel.direct_answer`) because it composes THIS product's structural
tools and depends on nothing from any upper layer. The harness *composes*
code_intel, so it imports the resolver **downward** and re-exports it here under
the historical ``control_plane.direct_answer`` name — every existing caller
(``control_plane.wake``, ``control_plane.orchestrator``, ``code_intel.direct``) keeps working
against a single implementation (G4-DUPLICATE-ANSWER-DIRECT-ENTRYPOINTS: no
second copy, no layering inversion).

The wake turn is answered *directly* from the code_intel structural API against
the pinned clone, citing a real ``file:line`` — no E2B sandbox, no Workroom
session (those are reserved for asked WORK, Doc 05; a direct answer that
provisioned a sandbox would violate AC-HOST-007). See
:mod:`code_intel.direct_answer` for the grounding discipline (Laws 1/2/3/4).
"""
from __future__ import annotations

from code_intel.direct_answer import DirectAnswer, answer_direct

__all__ = ["DirectAnswer", "answer_direct"]
