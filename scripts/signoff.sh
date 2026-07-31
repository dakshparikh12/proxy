#!/usr/bin/env bash
# signoff.sh — whole-product static + unit gate for the reactive-workroom system. Self-contained.
# Fail-closed: a missing tool or a failing gate is a FAIL, never a silent pass. The real-data
# verification (cal.com + enterprise-repo battery on live E2B + subscription) is run separately —
# it needs live services (see services/in-meeting/proof/).
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
V=".venv/bin"; FAIL=0
gate() { if "$@"; then echo "  [PASS]"; else echo "  [FAIL]"; FAIL=$((FAIL+1)); fi; }

echo "== ruff =="   ; [ -x "$V/ruff" ]  && gate "$V/ruff" check services libs scripts             || { echo "ruff missing";  FAIL=$((FAIL+1)); }
echo "== mypy =="   ; [ -x "$V/mypy" ]  && gate "$V/mypy" --strict services libs                  || { echo "mypy missing";  FAIL=$((FAIL+1)); }
echo "== bandit ==" ; [ -x "$V/bandit" ]&& gate "$V/bandit" -c pyproject.toml -qr services libs   || { echo "bandit missing";FAIL=$((FAIL+1)); }
echo "== unit + offline suite =="
gate "$V/python" -m pytest -m "not reality and not e2e and not negative and not integration" \
     -p no:cacheprovider -p no:testmon -q

echo "────────────────────────────────────"
if [ "$FAIL" -gt 0 ]; then echo "SIGN-OFF: NOT DONE — ${FAIL} gate(s) failed."; exit 1; fi
echo "SIGN-OFF: static + unit gates green (the real-data battery runs separately on live infra)."; exit 0
