#!/usr/bin/env bash
# Sole code arbiter. Exit 0 == a green pass. Gates, then milestone-ordered pytest.
set -uo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--selftest" ]; then echo "verify.sh selftest"; [ -d src ] || { echo "no src yet (expected)"; exit 1; }; fi
echo "== ruff ==";   ruff check src tests || exit 1
echo "== mypy ==";   mypy --strict src   || exit 1
echo "== bandit =="; bandit -q -r src     || exit 1
echo "== pytest (milestone order) =="
pytest -q -x --maxfail=1 || exit 1

# Guard: refuse green on zero collected tests
COLLECTED=$(pytest --collect-only -q 2>/dev/null | tail -1)
if echo "$COLLECTED" | grep -qE "^no tests ran|^0 "; then
  echo "NO TESTS COLLECTED — refusing green"; exit 1
fi

echo "ALL GREEN"; exit 0
