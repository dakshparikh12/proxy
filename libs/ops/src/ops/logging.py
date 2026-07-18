"""Structured JSON logging for the substrate (structlog)."""
from __future__ import annotations

from typing import Any

import structlog


def configure_logging() -> None:
    """Configure structlog for JSON-line output (one event per line)."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
