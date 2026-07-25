#!/usr/bin/env bash
# signoff.sh — Phase 3 whole-product static+unit gate. Self-contained (no slices/ or done-check).
# Fail-closed: a missing tool or a failing gate is a FAIL, never a silent pass. The lead runs the
# scenario corpus + Doc-09 journeys + deepeval on REAL infra separately (they need live services).
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
V=".venv/bin"; FAIL=0
run() { printf "== %s ==\n" "$1"; shift; "$@"; }
gate() { if "$@"; then echo "  [PASS]"; else echo "  [FAIL]"; FAIL=$((FAIL+1)); fi; }

[ -x "$V/ruff" ]  && gate "$V/ruff" check services libs scripts        || { echo "ruff missing";  FAIL=$((FAIL+1)); }
[ -x "$V/mypy" ]  && gate "$V/mypy" --strict services libs             || { echo "mypy missing";  FAIL=$((FAIL+1)); }
[ -x "$V/bandit" ]&& gate "$V/bandit" -qr services libs                || { echo "bandit missing";FAIL=$((FAIL+1)); }
gate "$V/python" -m pytest -m "not reality and not e2e and not negative and not integration" \
     -p no:cacheprovider -p no:testmon -q
[ -d build/scenarios ] && echo "== scenarios present — lead runs the corpus + deepeval on real infra =="

echo "────────────────────────────────────"
if [ "$FAIL" -gt 0 ]; then echo "SIGN-OFF: NOT DONE — ${FAIL} gate(s) failed."; exit 1; fi
echo "SIGN-OFF: static+unit gates green (scenarios/eval run separately on real infra)."; exit 0
