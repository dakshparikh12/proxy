"""Sentry error-reporting — one shared init + a before_send scrubber (AC-OBS-002/009).

``init_error_reporting()`` is the SINGLE place Sentry is initialised; every
service imports it rather than re-initialising per service. ``before_send``
scrubs both credential fields AND raw customer source out of every event before
it leaves the process, so a clone/parse error never ships source to Sentry.
"""
from __future__ import annotations

import re
from typing import Any, cast

import sentry_sdk

_SENSITIVE_KEYS = frozenset(
    {"authorization", "cookie", "session", "token", "api_key", "password", "secret"}
)

# Raw-source indicators: a code definition / body that must never leave the box.
# We redact from the source marker onward, keeping any surrounding framing text.
_SOURCE_MARKERS = re.compile(
    r"(def\s+\w+\s*\(|class\s+\w+\s*[\(:]|import\s+\w+|return\s+\w+).*",
    re.DOTALL,
)
_SOURCE_REDACTION = "[redacted-source]"


def init_error_reporting(dsn: str | None = None, environment: str | None = None) -> None:
    """Initialise Sentry ONCE (shared import) with the source/secret scrubber.

    Inert by default: with no DSN configured Sentry does not transmit, but the
    single init + before_send wiring is always in place.
    """
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        before_send=cast(Any, before_send),
        send_default_pii=False,
    )


def _scrub_source(value: Any) -> Any:
    """Recursively redact raw customer source out of any string in the payload."""
    if isinstance(value, str):
        return _SOURCE_MARKERS.sub(_SOURCE_REDACTION, value)
    if isinstance(value, dict):
        return {k: _scrub_source(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_source(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_source(v) for v in value)
    return value


def before_send(
    event: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Redact credential headers AND raw customer source before an event ships."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_KEYS:
                    headers[key] = "[redacted]"
    scrubbed = _scrub_source(event)
    return scrubbed if isinstance(scrubbed, dict) else event
