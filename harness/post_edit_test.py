#!/usr/bin/env python3
"""PostToolUse hook: fast, non-blocking test signal after an edit."""
import os
import subprocess
import pathlib

PY = os.environ.get("PROXY_PY", str(pathlib.Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"))
subprocess.run([PY, "-m", "pytest", "-q", "--no-header", "-x", "-k", "fast or smoke"], timeout=120)
