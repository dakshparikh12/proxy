"""File-coverage record + pure coverage computation (M4/M6).

Every tracked, non-excluded file gets exactly one row: ``indexed`` (parsed to
declarations) or ``flagged`` (grammarless / unsupported, still ripgrep-searchable).
``indexed + flagged == git ls-files`` is the readiness gate (AC-M4-006/AC-M6-002).
``compute_coverage`` is pure and model-free (AC-M6-003).

Two call shapes, ONE deterministic computation (§3.7.1):
  * ``compute_coverage(indexed=<int>, flagged=<int>)`` — the count-only form the M6
    determinism criterion (AC-M6-003) uses (``coverage_pct`` only, ``gaps=[]`` since a
    bare count carries no flag-reason detail to enumerate).
  * ``compute_coverage(coverage_rows=<list[CoverageRow]>, graph=<Graph>)`` — the FULL
    §3.7.1 form: ``coverage_pct`` = fraction indexed, PLUS the honest ``gaps`` list
    (flagged areas grouped by reason + exported graph nodes with no resolved edge).
Both are pure + deterministic (same inputs → same output, zero model calls).
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


def _group_by_flag_reason(rows: list[CoverageRow]) -> list[tuple[str, list[CoverageRow]]]:
    """Group flagged rows by their ``flag_reason`` — deterministic (reason-sorted)."""
    grouped: dict[str, list[CoverageRow]] = {}
    for r in rows:
        if r.status != "flagged":
            continue
        reason = r.flag_reason or "unknown"
        grouped.setdefault(reason, []).append(r)
    return sorted(grouped.items(), key=lambda kv: kv[0])


def _gaps_from_rows_and_graph(rows: list[CoverageRow], graph: Any) -> list[str]:
    """The honest blind-spot list (§3.7.1): flagged areas by reason + exported graph
    nodes with no resolved edge. Pure + deterministic; warning-only for graph orphans."""
    gaps: list[str] = []
    for reason, group in _group_by_flag_reason(rows):
        gaps.append(f"{len(group)} files flagged: {reason}")
    if graph is not None:
        edges = getattr(graph, "edges", [])
        nodes = getattr(graph, "nodes", [])
        resolved_targets = {e.target for e in edges} | {e.source for e in edges}
        orphan_exported = [
            n for n in nodes if getattr(n, "exported", 0) and n.id not in resolved_targets
        ]
        if orphan_exported:
            gaps.append(
                f"{len(orphan_exported)} exported symbols/tables with no resolved dependency edge"
            )
    return gaps


def compute_capability_tiers(tier1: bool, graph: Any) -> dict[str, str]:
    """Pre-compute the per-area ``who_writes`` capability tier (§3.7) at index time so the
    honesty labels are not guessed at query time. Pure + deterministic, no model call.

    The tier ladder (§3.6/§3.7):
      * ``exact-supported`` — an exact-supported ORM stack (Django / SQLAlchemy / Rails):
        ``who_writes`` is answered from resolved graph write-edges, tagged ``resolved``.
      * ``symbol-exact`` — not an exact-supported ORM, but the structural graph resolved
        table nodes (so table access is symbol-resolvable, still not exact-ORM).
      * ``search-only`` — no table nodes resolved: ``who_writes`` degrades to a ripgrep
        textual lead, always labelled ``lower-bound`` (never a silent wrong-exact).

    Keyed by ``"who_writes"`` (the one data-flow capability whose tier the spec pins). The
    single entry is deliberate: v0 has one repo-wide ORM stack, not per-directory stacks.
    """
    if graph is not None:
        has_table_nodes = any(getattr(n, "kind", "") == "table" for n in getattr(graph, "nodes", []))
    else:
        has_table_nodes = False
    if tier1:
        tier = "exact-supported"
    elif has_table_nodes:
        tier = "symbol-exact"
    else:
        tier = "search-only"
    return {"who_writes": tier}


def compute_coverage(
    indexed: int | None = None,
    flagged: int | None = None,
    llm_counter: Any = None,
    *,
    coverage_rows: list[CoverageRow] | None = None,
    graph: Any = None,
) -> CoverageResult:
    """Pure, deterministic coverage read — never routes through a model (§3.7.1, AC-M6-003).

    Accepts EITHER the count form (``indexed``/``flagged`` ints) OR the full form
    (``coverage_rows`` + ``graph``). The full form additionally enumerates the honest
    ``gaps`` (flagged-by-reason + orphan-exported graph nodes); the count form has no
    per-file detail so ``gaps`` is empty. ``coverage_pct`` = fraction of tracked files
    that are indexed (not flagged), identical formula in both forms.
    """
    if coverage_rows is not None:
        total = len(coverage_rows)
        n_indexed = sum(1 for r in coverage_rows if r.status == "indexed")
        pct = (n_indexed / total) if total else 1.0
        return CoverageResult(
            coverage_pct=pct, gaps=_gaps_from_rows_and_graph(coverage_rows, graph)
        )
    idx = indexed or 0
    flg = flagged or 0
    total = idx + flg
    pct = (idx / total) if total else 1.0
    return CoverageResult(coverage_pct=pct, gaps=[])
