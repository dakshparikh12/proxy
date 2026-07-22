"""doc03 reality-tier VCR config — shared by the scribe / qgate / close
``vendor:anthropic`` reality + negative tests.

Mirrors ``tests/reality/conftest.py``: cassettes live in the one central
``tests/cassettes/`` directory, and every credential-bearing header / query param is
scrubbed to ``REDACTED`` at RECORD time, so a real ``ANTHROPIC_API_KEY`` can never be
written into a committed cassette. Default ``record_mode`` is ``none`` (replay-only;
a missing cassette FAILS rather than silently hitting the network); the one-time
recording overrides it with ``--record-mode=once``.

``match_on`` deliberately omits ``body``: each ``@pytest.mark.vcr`` test owns exactly
one cassette file (named for the test), so method+URL is enough to select the single
interaction, and the request SHAPE is asserted explicitly in the test body (stronger
and less brittle than implicit vcr body-matching across SDK serialisation nuances).
"""
from __future__ import annotations

import os
import pathlib

import pytest

_CASSETTE_DIR = pathlib.Path(__file__).resolve().parent.parent / "cassettes"


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``integration`` marker locally.

    The verification ladder selects the db:postgres / gcs:objects tier with
    ``pytest -m integration``. ``reality``/``negative``/``e2e`` are registered in
    the root ``pyproject.toml``; ``integration`` is registered here (this conftest
    lives under ``tests/``) so a doc03 run stays free of unknown-marker warnings.
    """
    config.addinivalue_line(
        "markers",
        "integration: real db:postgres / gcs:objects tier (skips when its env is absent)",
    )

# Superset of every credential header the Anthropic SDK (and the other Proxy vendors)
# may send, scrubbed to REDACTED before anything is persisted to a cassette.
_SENSITIVE_HEADERS: list[str] = [
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "anthropic-api-key",
    "x-anthropic-api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
]
_SENSITIVE_QUERY: list[str] = ["api_key", "apikey", "token", "key", "access_token", "signature"]


def _scrub_response(response: dict) -> dict:
    """Blank any credential header the vendor echoes back before it is persisted."""
    headers = response.get("headers") or {}
    for key in list(headers.keys()):
        if key.lower() in {h.lower() for h in _SENSITIVE_HEADERS}:
            headers[key] = ["REDACTED"]
    return response


@pytest.fixture(scope="module")
def vcr_config() -> dict:
    return {
        # Replay-only by default (CI never hits the network); the one-time recording
        # sets PROXY_VCR_RECORD_MODE=once to capture the real vendor response.
        "record_mode": os.environ.get("PROXY_VCR_RECORD_MODE", "none"),
        "filter_headers": [(h, "REDACTED") for h in _SENSITIVE_HEADERS],
        "filter_query_parameters": [(q, "REDACTED") for q in _SENSITIVE_QUERY],
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
        "before_record_response": _scrub_response,
        "decode_compressed_response": True,
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(_CASSETTE_DIR)
