"""ToolCallTelemetry — a retained record for every tool call (AC-INV-013).

Every tool call the agent makes gets exactly one retained telemetry record
carrying its tool name, argv, and result (plus receipts). None is dropped: the
store is append-only and ``query()`` returns every record in call order, so the
tool-call trace is complete and auditable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallTelemetry:
    """An append-only store of one telemetry record per tool call."""

    _records: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        tool: str,
        argv: Any,
        result: Any,
        receipts: Any = None,
    ) -> None:
        """Append one retained record for a single tool call (never dropped)."""
        self._records.append(
            {
                "tool": tool,
                "argv": argv,
                "result": result,
                "receipts": receipts,
                "recorded_at": time.time(),
            }
        )

    def query(self) -> list[dict[str, Any]]:
        """Return every retained record, in call order (a defensive copy)."""
        return list(self._records)
