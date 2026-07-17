#!/usr/bin/env bash
# Sole code arbiter. Exit 0 == a green pass. Gates, then milestone-ordered pytest.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PROXY_PY:-.venv/bin/python}"
echo "== ruff ==";   "$PY" -m ruff check $(for d in services libs src; do [ -d $d ] && echo $d; done) tests || exit 1
echo "== mypy ==";   "$PY" -m mypy --strict $(for d in services libs src; do [ -d $d ] && echo $d; done)   || exit 1
echo "== bandit =="; "$PY" -m bandit -q -r src     || exit 1
echo "== pytest (milestone order) =="
"$PY" -m pytest -q -x --maxfail=1 || exit 1

# Guard: refuse green on zero collected tests
COLLECTED=$("$PY" -m pytest --collect-only -q 2>/dev/null | tail -1)
if echo "$COLLECTED" | grep -qE "^no tests ran|^0 "; then
  echo "NO TESTS COLLECTED — refusing green"; exit 1
fi

echo "ALL GREEN"; exit 0
