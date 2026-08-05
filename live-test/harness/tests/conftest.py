"""Shared fixtures + path wiring for the harness offline test suite."""
from __future__ import annotations

import sys
from pathlib import Path

# src-layout: put the package on the path without an editable install (this
# harness is a standalone tool under live-test/, not a uv workspace member).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

TRANSCRIPT_PATH = Path(__file__).resolve().parents[2] / "MEETING_TRANSCRIPT.md"
