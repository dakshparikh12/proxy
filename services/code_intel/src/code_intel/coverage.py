"""File-coverage record + pure coverage computation (M4/M6).

Every tracked, non-excluded file gets exactly one row: ``indexed`` (parsed to
declarations) or ``flagged`` (grammarless / unsupported, still ripgrep-searchable).
``indexed + flagged == git ls-files`` is the readiness gate (AC-M4-006/AC-M6-002).
``compute_coverage`` is pure and model-free (AC-M6-003).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoverageRow:
    path: str
    status: str  # "indexed" | "flagged"
    flag_reason: str | None = None


class CoverageRecord:
    def __init__(self, rows: list[CoverageRow] | None = None) -> None:
        self._rows: list[CoverageRow] = list(rows or [])
        self._by_path: dict[str, CoverageRow] = {r.path: r for r in self._rows}

    def add(self, row: CoverageRow) -> None:
        self._rows.append(row)
        self._by_path[row.path] = row

    def has_entry(self, path: str) -> bool:
        return path in self._by_path

    def get(self, path: str) -> CoverageRow | None:
        return self._by_path.get(path)

    def all_rows(self) -> list[CoverageRow]:
        return list(self._rows)

    def count_by_status(self, status: str) -> int:
        return sum(1 for r in self._rows if r.status == status)


@dataclass
class CoverageResult:
    coverage_pct: float
    gaps: list[str] = field(default_factory=list)


def compute_coverage(indexed: int, flagged: int, llm_counter: Any = None) -> CoverageResult:
    """Pure, deterministic coverage ratio — never routes through a model."""
    total = indexed + flagged
    pct = (indexed / total) if total else 1.0
    return CoverageResult(coverage_pct=pct, gaps=[])
