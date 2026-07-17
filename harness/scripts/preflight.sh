#!/usr/bin/env bash
# Preflight launch checklist. PASS/FAIL for each check; exit 0 only if all non-WARN pass.
set -uo pipefail
cd "$(dirname "$0")/../.."

FAILS=0
WARNS=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $label"
  else
    echo "FAIL  $label"
    FAILS=$((FAILS + 1))
  fi
}

warn_check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $label"
  else
    echo "WARN  $label"
    WARNS=$((WARNS + 1))
  fi
}

# 1. >0 tests collected AND verify.sh exits nonzero (red = arbiter alive)
echo "--- Preflight checks ---"

# Tests collected via venv python — derive expected count from sealed bundle
PY="${PROXY_PY:-.venv/bin/python}"
BUNDLE="acceptance/doc01/criteria/criteria.yaml"

# 1a. Collect tests — FAIL on any collection error
COLLECT_OUTPUT=$("$PY" -m pytest --collect-only -q 2>&1)
COLLECT_ERRORS=$(echo "$COLLECT_OUTPUT" | grep -cE "^ERROR collecting" || true)
COLLECTED_NUM=$(echo "$COLLECT_OUTPUT" | grep -oE "^[0-9]+ tests? collected" | grep -oE "^[0-9]+")

if [ "$COLLECT_ERRORS" -gt 0 ]; then
  echo "FAIL  pytest collection errors ($COLLECT_ERRORS errors)"
  echo "$COLLECT_OUTPUT" | grep "^ERROR collecting"
  FAILS=$((FAILS + 1))
elif [ -z "$COLLECTED_NUM" ] || [ "$COLLECTED_NUM" -eq 0 ]; then
  echo "FAIL  0 tests collected"
  FAILS=$((FAILS + 1))
else
  echo "PASS  $COLLECTED_NUM tests collected, 0 collection errors"
fi

# 1b. Bundle coverage — every rung-1 criterion must map to ≥1 test function
if [ -f "$BUNDLE" ]; then
  COVERAGE_RESULT=$("$PY" -c "
import re, pathlib, sys
RUNG1 = {'[unit-example]','[unit-property]','[contract]','[unit-fixture]','[security-adversarial]','[adversarial]'}
text = pathlib.Path('$BUNDLE').read_text()
ids = re.findall(r'criterion_id:\s+(AC-\S+)', text)
classes = re.findall(r'evidence_class:\s+\"(\[[^\]]+\])\"', text)
rung1 = [(i, c) for i, c in zip(ids, classes) if c in RUNG1]
test_funcs = set()
for tf in pathlib.Path('tests').glob('test_*.py'):
    for m in re.finditer(r'^def (test_\w+)', tf.read_text(), re.MULTILINE):
        test_funcs.add(m.group(1))
uncovered = []
for cid, ec in rung1:
    pat = cid.lower().replace('-', '_')
    if not any(pat in f for f in test_funcs):
        uncovered.append(cid)
if uncovered:
    print(f'FAIL {len(uncovered)} rung-1 criteria uncovered: {\" \".join(uncovered)}')
    sys.exit(1)
else:
    print(f'PASS {len(rung1)} rung-1 criteria, all covered')
    sys.exit(0)
" 2>&1)
  COVERAGE_EXIT=$?
  echo "$COVERAGE_RESULT"
  if [ "$COVERAGE_EXIT" -ne 0 ]; then
    FAILS=$((FAILS + 1))
  fi
else
  echo "WARN  bundle not found at $BUNDLE — skipping coverage check"
  WARNS=$((WARNS + 1))
fi

# verify.sh exits nonzero (red = arbiter alive)
if bash harness/verify.sh >/dev/null 2>&1; then
  echo "FAIL  verify.sh exits nonzero (arbiter must be red before build)"
  FAILS=$((FAILS + 1))
else
  echo "PASS  verify.sh exits nonzero (arbiter alive)"
fi

# 2. eval_runner.py exits 2
"$PY" eval_runner.py --component dummy 2>/dev/null; EVAL_EXIT=$?
if [ "$EVAL_EXIT" -eq 2 ]; then
  echo "PASS  eval_runner.py exits 2"
else
  echo "FAIL  eval_runner.py exits $EVAL_EXIT (expected 2)"
  FAILS=$((FAILS + 1))
fi

# 3. guard.py present and probe-passing
if [ -f harness/guard.py ]; then
  PROBE='{"tool_name":"Read","tool_input":{"file_path":"/dev/null"}}'
  if echo "$PROBE" | "$PY" harness/guard.py >/dev/null 2>&1; then
    echo "PASS  guard.py present and probe-passing"
  else
    echo "FAIL  guard.py probe failed"
    FAILS=$((FAILS + 1))
  fi
else
  echo "FAIL  guard.py not found (is it .off?)"
  FAILS=$((FAILS + 1))
fi

# 4. git status clean
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
  echo "PASS  git status clean"
else
  echo "FAIL  git status not clean"
  FAILS=$((FAILS + 1))
fi

# 5. integrity hash recorded (just verify protected trees exist)
HASH=$("$PY" -c "
import pathlib, hashlib
ROOT = pathlib.Path('.')
trees = ('tests/', 'harness/', 'fixtures/', 'criteria/', 'acceptance/', 'product/', '.claude/')
h = hashlib.sha256()
count = 0
for t in trees:
    tp = ROOT / t
    if not tp.exists(): continue
    for p in sorted(tp.rglob('*')):
        if p.is_file():
            h.update(str(p).encode())
            h.update(p.read_bytes())
            count += 1
print(f'{h.hexdigest()} ({count} files)')
" 2>/dev/null)
if [ -n "$HASH" ]; then
  echo "PASS  integrity hash recorded: $HASH"
else
  echo "FAIL  integrity hash computation failed"
  FAILS=$((FAILS + 1))
fi

# 6. acceptance-bundle check (WARN not FAIL while acceptance/ is empty)
if [ -d acceptance/doc01 ] && { [ -f acceptance/doc01/manifest ] || [ -f acceptance/doc01/manifest.yaml ]; }; then
  MANIFEST_FILE=$([ -f acceptance/doc01/manifest.yaml ] && echo "acceptance/doc01/manifest.yaml" || echo "acceptance/doc01/manifest")
  # Check that manifest contains hashes (basic sanity)
  if grep -qE '[a-f0-9]{32,}' "$MANIFEST_FILE" 2>/dev/null; then
    echo "PASS  acceptance bundle manifest with hashes"
  else
    echo "WARN  acceptance/doc01/manifest present but no hashes found"
    WARNS=$((WARNS + 1))
  fi
else
  echo "WARN  acceptance/doc01/manifest not present (acceptance/ empty — expected during setup)"
  WARNS=$((WARNS + 1))
fi

echo "---"
echo "Result: $FAILS failures, $WARNS warnings"
if [ "$FAILS" -eq 0 ]; then
  echo "PREFLIGHT OK"
  exit 0
else
  echo "PREFLIGHT FAILED"
  exit 1
fi
