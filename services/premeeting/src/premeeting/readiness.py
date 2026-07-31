"""The ready signal for the connect-status poll (PM-READY-01).

The connect poll renders the canonical Readiness states (``connecting → cloning → indexing →
ready`` plus a terminal ``not_ready`` that NAMES the gaps). This module maps a
:class:`~premeeting.pipeline.PipelineResult` onto that poll surface — the REAL states the
pipeline emitted + the REAL verify verdict, never a fabricated number (Law 2). There is
deliberately NO ``mapping`` state: the map-build IS the ``indexing`` phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pipeline import PipelineResult

# The canonical Readiness enum (CANONICAL §1.5) — ``mapping`` is deliberately absent.
VALID_STATES: frozenset[str] = frozenset(
    {"connecting", "cloning", "indexing", "ready", "not_ready"}
)


@dataclass
class ReadinessSignal:
    """The poll-facing readiness — the terminal status, the ordered states, the named gaps."""

    status: str  # 'ready' | 'not_ready'
    states: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    sha: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def signal_from_result(result: PipelineResult) -> ReadinessSignal:
    """Map a pipeline outcome onto the connect-poll readiness (real states + real gaps).

    ``ready`` iff the pipeline reached a clean verify; otherwise ``not_ready`` carrying the
    NAMED gaps the verify/pipeline produced (never an empty, unexplained not_ready)."""
    gaps = list(result.reasons) if not result.ready else []
    if not result.ready and not gaps:
        gaps = ["not ready: no reason recorded"]
    return ReadinessSignal(
        status="ready" if result.ready else "not_ready",
        states=list(result.states),
        gaps=gaps,
        sha=result.sha,
    )


__all__ = ["VALID_STATES", "ReadinessSignal", "signal_from_result"]
