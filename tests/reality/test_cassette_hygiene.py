"""Cassette-hygiene gate (Task 2): NO real secret may ever be committed inside a cassette.

vcrpy scrubs credentials at record time via the ``vcr_config`` filters in conftest.py, but a filter
gap or a hand-edited cassette could still leak a key. This test is the independent backstop: it scans
every committed cassette for live-credential shapes and vendor key prefixes and fails if any appears.

It runs today with zero cassettes (passes vacuously) and starts protecting the moment the first
cassette is recorded — so a leak is caught by CI before it can be pushed, not after.
"""
from __future__ import annotations

import pathlib
import re

CASSETTE_DIR = pathlib.Path(__file__).resolve().parent.parent / "cassettes"

# Live-credential shapes. These are deliberately broad: an Authorization value that is NOT the
# scrubbed sentinel, long high-entropy hex/base64 tokens, and known vendor key prefixes.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("unredacted bearer/token auth", re.compile(r"(?i)authorization:\s*(bearer|token)\s+(?!REDACTED)\S{12,}")),
    ("unredacted x-api-key header", re.compile(r"(?i)x-api-key:\s*(?!REDACTED)\S{12,}")),
    ("openai-style key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("recall key prefix", re.compile(r"(?i)recall[_-]?(sk|key)[_-][A-Za-z0-9]{16,}")),
    ("assemblyai hex key", re.compile(r"\b[a-f0-9]{32}\b")),
    ("long high-entropy token in api_key= param", re.compile(r"(?i)api_key=(?!REDACTED)[A-Za-z0-9_\-]{20,}")),
]

# Values our own filters write — never flag these.
ALLOWED = {"REDACTED"}


def _cassette_files() -> list[pathlib.Path]:
    if not CASSETTE_DIR.exists():
        return []
    return sorted(p for p in CASSETTE_DIR.rglob("*") if p.suffix in {".yaml", ".yml", ".json"})


def test_no_secret_leaks_in_any_committed_cassette() -> None:
    offenders: list[str] = []
    for path in _cassette_files():
        text = path.read_text(errors="replace")
        for label, pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                snippet = m.group(0)
                if any(a in snippet for a in ALLOWED):
                    continue
                line_no = text[: m.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(CASSETTE_DIR.parent)}:{line_no} — {label}: {snippet[:40]}…")
    assert not offenders, (
        "Potential real secret(s) found inside committed cassette(s). vcrpy filter_headers/"
        "filter_query_parameters should have scrubbed these to REDACTED at record time:\n  "
        + "\n  ".join(offenders)
    )


def test_cassette_dir_exists_and_is_documented() -> None:
    """The cassette home and its recording procedure must exist (machinery, independent of content)."""
    assert CASSETTE_DIR.exists(), f"cassette directory missing: {CASSETTE_DIR}"
    assert (CASSETTE_DIR / "RECORDING.md").is_file(), "one-time recording procedure not documented"
