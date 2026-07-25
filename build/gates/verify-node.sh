#!/usr/bin/env bash
# verify-node.sh — run ONE chain node's acceptance_cmd on the real path, fail-closed.
# Self-contained: no slices/tasks.json, no done-check coupling. Usage:
#   bash build/gates/verify-node.sh <pytest selector args...>     e.g. tests/scribe -k fold
#
# Closes two holes we have hit:
#   - false-DONE via silent no-op: pytest exit 5 (NO tests collected) is a FAIL, never a pass.
#   - workspace prune / freeze: runs `.venv/bin/python -m pytest` (never `uv run`) with no:testmon.
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
[ $# -ge 1 ] || { echo "FAIL: no acceptance_cmd given"; exit 2; }
RUN="${PROXY_RUN:-.venv/bin/python -m}"
$RUN pytest "$@" -p no:cacheprovider -p no:testmon -q; RC=$?
case $RC in
  0) echo "PASS: node acceptance green ($*)"; exit 0;;
  5) echo "FAIL: exit 5 — acceptance_cmd matched NO tests; a no-op is not a pass"; exit 1;;
  *) echo "FAIL: acceptance exited $RC"; exit 1;;
esac
