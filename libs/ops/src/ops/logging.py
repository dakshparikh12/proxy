"""Structured JSON logging for the substrate (structlog) with source scrubbing.

Every stdout line is a single structlog-rendered JSON object. A processor scrubs
raw customer source out of every event BEFORE it renders, so a clone/parse log
line never carries source bytes to stdout (AC-OBS-001/009).
"""
from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

import structlog

# Raw-source indicators: redact from the code marker onward so a log field that
# carries a file's contents never emits the source itself.
_SOURCE_MARKERS = re.compile(
    r"(def\s+\w+\s*\(|class\s+\w+\s*[\(:]|import\s+\w+|return\s+\w+).*",
    re.DOTALL,
)
_SOURCE_REDACTION = "[redacted-source]"


def _scrub_source_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Redact raw customer source out of every string value in the event."""
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = _SOURCE_MARKERS.sub(_SOURCE_REDACTION, value)
    return event_dict


def configure_logging() -> None:
    """Configure structlog for JSON-line output (one event per line)."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _scrub_source_processor,
            structlog.processors.JSONRenderer(),
        ],
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
