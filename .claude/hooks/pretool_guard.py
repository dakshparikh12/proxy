#!/usr/bin/env python3
"""Builder-read-only guard.

PreToolUse hook: blocks Edit|Write|MultiEdit|NotebookEdit when the target
path is under a builder-read-only directory or matches _baseline.json.
"""

from __future__ import annotations

import json
import os
import sys

# Tools that write to the filesystem
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Directories that are builder-read-only (relative to repo root or absolute)
PROTECTED_DIRS = (
    "tests/",
    "acceptance/",
    "fixtures/",
    "goldens/",
    "criteria/",
    "product/",
)

PROTECTED_SUFFIX = "_baseline.json"


def _is_protected(file_path: str) -> bool:
    """Return True if file_path is under a protected dir or matches _baseline.json."""
    # Normalize to a relative-style path for matching
    norm = os.path.normpath(file_path)
    # Use the basename of the normalized path for suffix check
    if os.path.basename(norm) == "_baseline.json" or norm.endswith("_baseline.json"):
        return True

    # Normalize separators so matching is consistent on any OS
    norm_fwd = norm.replace(os.sep, "/")

    for protected in PROTECTED_DIRS:
        # Match at any depth: protected dir segment appears in path
        # e.g. "tests/" matches "/repo/tests/doc03/test_x.py" and "tests/foo.py"
        if f"/{protected}" in f"/{norm_fwd}":
            return True

    return False


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        # Fail-open: malformed payload → allow
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in WRITE_TOOLS:
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    if _is_protected(file_path):
        print(json.dumps({"decision": "block", "reason": f"builder-read-only path: {file_path}"}))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
