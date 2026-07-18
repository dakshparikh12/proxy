"""Abort registry — single home for cooperative task abortion (AC-CMP-010)."""
from __future__ import annotations


class AbortRegistry:
    """Tracks task ids that have been asked to abort (barge-in / quiet / preempt)."""

    def __init__(self) -> None:
        self._aborted: set[str] = set()

    def abort(self, task_id: str) -> None:
        self._aborted.add(task_id)

    def clear(self, task_id: str) -> None:
        self._aborted.discard(task_id)

    def is_aborted(self, task_id: str) -> bool:
        return task_id in self._aborted
