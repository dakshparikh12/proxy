"""Graph-version garbage collection (M11).

A retained per-SHA graph version is dropped once no live meeting pins it — the
current head is always kept (AC-GV-002).
"""
from __future__ import annotations

from typing import Any


class GraphGarbageCollector:
    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def run(self) -> None:
        self._pipeline.gc()
