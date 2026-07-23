#!/usr/bin/env python3
"""forge PostToolUse signal — the fast static rung, run on every edited Python file.

Runs ruff + mypy --strict + bandit + testmon-affected tests. NON-BLOCKING: always
exits 0 and only prints a signal. The blocking gates live in done-check (Semgrep +
the full suite); this is the per-edit early-warning so the builder sees a regression
the moment it lands, not at DONE.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 0, ""  # tool not installed → skip silently (non-blocking)
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:  # noqa: BLE001
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    failures: list[str] = []

    rc, out = _run(["ruff", "check", file_path])
    if rc != 0 and out:
        failures.append(f"[ruff]\n{out}")

    rc, out = _run(["mypy", "--strict", file_path])
    if rc != 0 and out:
        failures.append(f"[mypy]\n{out}")

    # bandit: only flag MEDIUM+ severity to avoid noise on the per-edit signal
    rc, out = _run(["bandit", "-q", "-ll", file_path])
    if rc != 0 and out:
        failures.append(f"[bandit]\n{out}")

    # testmon-affected offline tests
    OFFLINE = "not reality and not e2e and not negative and not integration"
    rc, out = _run(["uv", "run", "pytest", "-q", "--testmon", "-p", "no:cacheprovider", "-m", OFFLINE])
    if rc not in (0, 5) and out:
        failures.append(f"[pytest-testmon]\n{out[-1500:]}")

    if failures:
        print("forge post-edit signal (non-blocking):\n" + "\n\n".join(failures))
    sys.exit(0)


if __name__ == "__main__":
    main()
