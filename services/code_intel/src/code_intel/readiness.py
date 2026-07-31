"""Readiness collection + gate (M6).

Emits only the canonical states (``connecting|cloning|indexing|ready|not_ready``);
``ready`` is reached only when ``indexed + flagged == git ls-files`` (the coverage
gate, AC-M6-002). The ``ready`` record carries ``indexed_at`` + the 40-hex
``pinned_sha`` (AC-M6-004).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

VALID_STATES = ("connecting", "cloning", "indexing", "ready", "not_ready")


@dataclass
class ReadinessRecord:
    indexed_at: datetime | None = None
    pinned_sha: str | None = None
    coverage_pct: float = 0.0
    # The honest blind-spot list (§3.7.1) — reported alongside coverage_pct, computed by the
    # deterministic ``coverage.compute_coverage(coverage_rows=…, graph=…)`` read (no LLM).
    gaps: list[str] = field(default_factory=list)
    # Per-area/stack ``who_writes`` capability tier (§3.7) — pre-computed at index time so the
    # honesty labels are not guessed at query time. Maps an area/stack key to one of
    # "exact-supported" | "symbol-exact" | "search-only".
    capability_tiers: dict[str, str] = field(default_factory=dict)


class ReadinessCollector:
    def __init__(self) -> None:
        self.emitted_states: list[str] = []
        self.emitted_error: Any = None

    def emit(self, state: str) -> None:
        self.emitted_states.append(state)

    # aliases the pipeline may use
    def record(self, state: str) -> None:
        self.emit(state)

    def on_state(self, state: str) -> None:
        self.emit(state)

    def set_error(self, error: Any) -> None:
        self.emitted_error = error


def now_indexed_at() -> datetime:
    return datetime.now(UTC)
