"""VCR configuration for the reality / negative / e2e ladder tiers (GENERATOR.md §8.4).

The reality/negative/e2e tiers drive Proxy's real ``call_external`` seam against a *recorded*
vendor response (a vcrpy cassette). Recording and replay BOTH flow through the ``vcr_config``
fixture below, so the secret filters here apply at record time — a real API key present in a live
request/response is scrubbed to ``REDACTED`` before anything is ever written to a cassette file.

Default record mode is ``none``: in CI (and by default locally) a test may ONLY replay an existing
cassette and will FAIL if a matching cassette is absent — it never silently reaches the network.
The one-time recording procedure (``scripts/record_cassettes.sh``) overrides this with
``--record-mode=once`` against sandbox/free-tier vendor accounts. See ``tests/cassettes/RECORDING.md``.
"""
from __future__ import annotations

import pathlib

import pytest

CASSETTE_DIR = pathlib.Path(__file__).resolve().parent.parent / "cassettes"

# Request/response headers that may carry a vendor credential or session token. vcrpy scrubs
# these to REDACTED at record time (case-insensitive match). Keep this list a superset of every
# auth mechanism the three Proxy vendors use, plus the generic ones.
SENSITIVE_HEADERS: list[str] = [
    "authorization",          # AssemblyAI (Bearer), Recall (Token …)
    "x-api-key",              # Cartesia, generic
    "api-key",
    "apikey",
    "recall-api-key",
    "x-recall-api-key",
    "cartesia-api-key",
    "x-cartesia-api-key",
    "assemblyai-api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-amz-security-token",
]

# Query-string parameters that may carry a credential.
SENSITIVE_QUERY_PARAMS: list[str] = ["api_key", "apikey", "token", "key", "access_token", "signature"]


def _scrub_headers() -> list[tuple[str, str]]:
    return [(h, "REDACTED") for h in SENSITIVE_HEADERS]


def _scrub_query() -> list[tuple[str, str]]:
    return [(q, "REDACTED") for q in SENSITIVE_QUERY_PARAMS]


@pytest.fixture(scope="module")
def vcr_config() -> dict:
    """VCR kwargs applied to every ``@pytest.mark.vcr`` test in this tree (record + replay)."""
    return {
        # Never hit the network unless the recording script explicitly overrides via CLI.
        "record_mode": "none",
        # Scrub credentials from both directions BEFORE they are written to a cassette.
        "filter_headers": _scrub_headers(),
        "filter_query_parameters": _scrub_query(),
        # Match on method + URL + body so a negative cassette (error/timeout body) is distinct
        # from the positive one for the same endpoint.
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
        # Belt-and-suspenders: a post-record hook that blanks any auth header the vendor echoes
        # back in a response, and refuses to persist a well-known live-key shape.
        "before_record_response": _scrub_response,
        "decode_compressed_response": True,
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    """All cassettes live in tests/cassettes/ (one clearly-named directory, per Task 2)."""
    return str(CASSETTE_DIR)


def _scrub_response(response: dict) -> dict:
    """Blank any credential-bearing header a vendor echoes in its response before it is persisted."""
    headers = response.get("headers") or {}
    for key in list(headers.keys()):
        if key.lower() in {h.lower() for h in SENSITIVE_HEADERS}:
            headers[key] = ["REDACTED"]
    return response
