"""Spike gate logic and deterministic fallbacks (AC-BLD-001, AC-BLD-002).

Two independent branches gate the substrate commit:

* branch (a) - direct-answer p50 latency must be <= ~2.5s over real questions;
  a slower median is an architecture problem, not a tuning knob.
* branch (b) - ``get_dependents`` / ``who_writes`` accuracy graded against the
  mechanically-derived blast-radius golden. Dropping a real dependent fails and
  ESCALATES to the user as a challenge-core question; it is never silently
  patched to pass.

The substrate is not committed until both branches hold (or a named,
deterministic fallback is applied). Unknown ad-hoc failures raise rather than
resolving to a silent pass-through patch.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# Branch (a) threshold: direct-answer p50 must be at or under ~2.5s.
DIRECT_ANSWER_P50_MAX_SECONDS: float = 2.5

# Named, deterministic fallbacks keyed by the failing spike branch. Each value
# describes the degraded-but-honest posture we adopt; none is a silent patch.
_FALLBACKS: dict[str, str] = {
    "latency-fail": "shallow-only + ACK-tile",
    "orm-fail": "restrict who_writes matrix + label lower-bound",
    "sdk-shape-differs": "write an adapter or fail the build",
}


def direct_answer_p50_gate(samples: list[float]) -> bool:
    """Return True iff the direct-answer p50 latency is within the budget.

    Branch (a): passes when ``median(samples) <= DIRECT_ANSWER_P50_MAX_SECONDS``.
    A slower median is an architecture problem, so the gate fails closed.
    """
    return statistics.median(samples) <= DIRECT_ANSWER_P50_MAX_SECONDS


@dataclass(frozen=True)
class OrmAccuracyResult:
    """Outcome of branch (b): who_writes/get_dependents accuracy grading."""

    passed: bool

    @property
    def escalate_as_challenge_core(self) -> bool:
        """A branch-(b) failure escalates to the user, never a silent patch."""
        return not self.passed


def orm_accuracy_gate(
    *, measured_direct: set[str], golden_direct: set[str]
) -> OrmAccuracyResult:
    """Grade measured direct importers against the mechanical golden.

    Branch (b): passes only when the measured set exactly equals the golden set
    (no real dependent dropped). On failure the result flags escalation as a
    challenge-core question rather than allowing a silent patch to pass.
    """
    return OrmAccuracyResult(passed=measured_direct == golden_direct)


def substrate_commit_allowed(*, branch_a: bool, branch_b: bool) -> bool:
    """Return True iff both spike branches hold, gating the substrate commit."""
    return branch_a and branch_b


def select_fallback(failure: str) -> str:
    """Return the named, deterministic fallback for a failing spike branch.

    Raises ``KeyError`` for any unknown/ad-hoc failure so that "just make it
    green" cannot resolve to a silent pass-through patch.
    """
    return _FALLBACKS[failure]
