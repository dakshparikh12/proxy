"""Sentry error-reporting hook — scrubs secrets before an event leaves."""
from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = frozenset(
    {"authorization", "cookie", "session", "token", "api_key", "password", "secret"}
)


def before_send(
    event: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Redact sensitive fields from the event's request headers/extra."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_KEYS:
                    headers[key] = "[redacted]"
    return event
