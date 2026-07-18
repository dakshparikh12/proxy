"""Shared code_intel result / citation value-types.

One module every tool result shape imports so the field surface is defined once
(§5a of the doc01 plan). ``Confidence`` is the honesty tag every grounded claim
carries (Law 2 — never overstate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Confidence = Literal["resolved", "lower-bound", "not-found-by-this-method"]
Status = Literal["ok", "not-found"]


@dataclass
class ResultItem:
    """A single grounded graph result (dependent / entry-point)."""

    id: str
    path: str
    file: str
    line: int
    pagerank: float = 0.0
    confidence: str = "resolved"


@dataclass
class DependentsResult:
    results: list[ResultItem] = field(default_factory=list)
    status: str = "ok"
    truncated_count: int = 0
    graph_sha: str | None = None


@dataclass
class EntryPointsResult:
    results: list[ResultItem] = field(default_factory=list)
    status: str = "ok"
    graph_sha: str | None = None


@dataclass
class Writer:
    id: str
    file: str
    line: int
    confidence: str


@dataclass
class WhoWritesResult:
    writers: list[Writer] = field(default_factory=list)
    status: str = "ok"


@dataclass
class ModuleRef:
    id: str
    confidence: str


@dataclass
class SharesTableResult:
    modules: list[ModuleRef] = field(default_factory=list)
    status: str = "ok"


@dataclass
class OwnerResult:
    owner: str
    confidence: str
    file: str | None = None
    line: int | None = None


@dataclass
class RefItem:
    file: str
    line: int
    confidence: str
    context: str | None = None
    id: str | None = None
    path: str | None = None


@dataclass
class FindReferencesResult:
    results: list[RefItem] = field(default_factory=list)
    status: str = "ok"
    _from_stale_lsp_cache: bool = False


@dataclass
class BatchFile:
    path: str
    content: str | None = None
    error: str | None = None


@dataclass
class BatchReadResult:
    files: list[BatchFile] = field(default_factory=list)
    truncated: bool = False
    truncated_count: int | None = 0


@dataclass
class Notification:
    text: str


@dataclass
class ToolManifest:
    tool_names: list[str] = field(default_factory=list)
