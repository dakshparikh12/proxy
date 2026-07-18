"""BehaviorRunner — the sole site where ``stream_deltas`` is applied (AC-CMP-005).

The delta-izer is applied exactly once, here inside ``run``; downstream
consumers read the delta stream and MUST NOT re-wrap it (C2 amendment).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from libs.contracts import AgentChunk

from .config import BehaviorConfig
from .deltas import stream_deltas


class BehaviorRunner:
    def __init__(self, config: BehaviorConfig) -> None:
        self._config = config

    @property
    def config(self) -> BehaviorConfig:
        return self._config

    def run(self, raw: Iterable[AgentChunk]) -> Iterator[AgentChunk]:
        # The one and only application of the delta-izer in the whole tree.
        yield from stream_deltas(raw)
