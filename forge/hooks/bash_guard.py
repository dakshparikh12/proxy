#!/usr/bin/env python3
"""Full-suite guard (PreToolUse on Bash).

Agents in the audit/build phase repeatedly reach for "run the whole offline
suite to check for breakage" despite prompt instructions — a `uv run pytest
--testmon -m "not reality and not e2e ..."` cold run turns the 2.5-min suite
into ~19 min and FREEZES the workflow on one agent (observed 3x).

Prompts don't hold, so this blocks it structurally: a DIRECT full-suite pytest
tool-call is denied with guidance to use targeted tests or the gate. The gate
stays allowed — `done-check.sh` runs the suite as an internal subprocess (not a
Bash *tool call*), so this hook never sees it; and a command that mentions
done-check is explicitly waved through. Targeted runs
(`.venv/bin/pytest <file>::<test>`) are unaffected.
"""
from __future__ import annotations

import json
import re
import sys

# The offline-suite selector (only the whole-suite run uses this marker string);
# --testmon is the slow cold-start incremental runner (the gate disables it with
# -p no:testmon, and nothing targeted needs it). Either signals a full-suite run.
_FULL_SUITE = re.compile(r"not\s+reality|not\s+e2e\s+and\s+not|--testmon")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # fail-open

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    cmd = (payload.get("tool_input", {}) or {}).get("command", "") or ""
    if "pytest" not in cmd:
        sys.exit(0)
    # the gate is the ONE legitimate full-suite path — always allow it
    if "done-check" in cmd:
        sys.exit(0)
    if _FULL_SUITE.search(cmd):
        print(json.dumps({
            "decision": "block",
            "reason": (
                "Forbidden: running the FULL offline suite directly (--testmon or "
                "-m \"not reality/e2e...\") freezes the loop (~19min cold-start). The "
                "full suite is the GATE's job only: `bash forge/gates/done-check.sh "
                "--spec <id>` (background+poll). In audit/build, run ONLY targeted "
                "tests: `.venv/bin/pytest <file>::<test>` (no --testmon, no broad -m)."
            ),
        }))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
