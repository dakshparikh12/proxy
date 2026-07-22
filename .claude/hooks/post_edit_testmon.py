#!/usr/bin/env python3
"""PostToolUse signal: ruff + mypy + testmon on edited Python files.

Non-blocking — always exits 0. Prints a signal summary on failures.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a command and return (returncode, combined stdout+stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    failures: list[str] = []

    # ruff check
    rc, out = _run(["ruff", "check", file_path])
    if rc != 0 and out:
        failures.append(f"[ruff]\n{out}")

    # mypy --strict
    rc, out = _run(["mypy", "--strict", file_path])
    if rc != 0 and out:
        failures.append(f"[mypy]\n{out}")

    # pytest with testmon (offline markers only)
    OFFLINE_MARKERS = "not reality and not e2e and not negative and not integration"
    rc, out = _run([
        "uv", "run", "pytest", "-q", "--testmon",
        "-p", "no:cacheprovider",
        "-m", OFFLINE_MARKERS,
    ])
    # exit 5 = no tests selected — treat as OK
    if rc not in (0, 5) and out:
        failures.append(f"[pytest-testmon]\n{out}")

    if failures:
        print("post-edit signal:\n" + "\n\n".join(failures))

    sys.exit(0)


if __name__ == "__main__":
    main()
