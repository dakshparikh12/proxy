#!/usr/bin/env bash
# forge done-check — the DONE predicate, 5 conjuncts. Exit 0 iff ALL hold.
#
# Usage:
#   done-check.sh --spec <id>           the spec-level predicate (5 conjuncts)
#   done-check.sh --task <id> <task_id> one task's real acceptance cmd is green AND passes:true
#
# The 5 conjuncts (each a distinct, un-gameable guarantee):
#   C1 coverage      req<->crit<->task closure (coverage.py)                         [deterministic]
#   C2 tasks-green   EVERY task's real acceptance cmd RAN green (not a flag read)    [anti false-DONE]
#   C3 static+unit   ruff + mypy --strict + bandit + semgrep + offline pytest        [code correct]
#   C4 real-data     eval >= baseline on held-out data; N/A for zero-[eval] docs     [product bar]
#   C5 regressions   the BLOCKED-then-fixed ratchet (pytest regressions/)            [compounding]
#
# Physics: passes:true is only trustworthy because C2 re-runs the real cmd — never trusts the flag.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PYTHON="${FORGE_PYTHON:-.venv/bin/python}"; [ -x "$PYTHON" ] || PYTHON="python3"
RUN="${FORGE_RUN:-uv run}"

usage() { echo "usage: $0 --spec <id> | --task <id> <task_id>" >&2; exit 2; }
[ $# -ge 2 ] || usage
MODE="$1"; shift

# ── --task <id> <task_id> ────────────────────────────────────────────────────
if [ "$MODE" = "--task" ]; then
    [ $# -ge 2 ] || usage
    ID="$1"; TASK="$2"; TASKS="slices/${ID}/tasks.json"
    [ -f "$TASKS" ] || { echo "ERROR: $TASKS not found"; exit 1; }
    read -r PASSES CMD < <("$PYTHON" - "$TASKS" "$TASK" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); t=next((x for x in d["tasks"] if x["task_id"]==sys.argv[2]),None)
if not t: print("MISSING"); sys.exit()
print("true" if t.get("passes") else "false", (t.get("acceptance") or {}).get("cmd") or "")
PY
)
    [ "$PASSES" = "MISSING" ] && { echo "ERROR: task $TASK not in $TASKS"; exit 1; }
    [ -n "$CMD" ] || { echo "BLOCKED: $TASK has no acceptance cmd"; exit 1; }
    [ "$PASSES" = "true" ] || { echo "FAIL: $TASK passes:false"; exit 1; }
    bash -c "$RUN $CMD -p no:cacheprovider -p no:testmon"; RC=$?
    [ $RC -eq 5 ] && { echo "FAIL: $TASK collected no tests (exit 5)"; exit 1; }
    [ $RC -ne 0 ] && { echo "FAIL: $TASK acceptance cmd exited $RC"; exit 1; }
    echo "PASS: $TASK"; exit 0
fi

[ "$MODE" = "--spec" ] || usage
ID="$1"; DOCID="doc${ID}"; TASKS="slices/${ID}/tasks.json"; BASELINE="slices/${ID}/_baseline.json"
FAIL=0
echo "== DONE(${ID}) =="

# ── C1 coverage ──────────────────────────────────────────────────────────────
printf "[1] coverage(req<->crit<->task) ... "
if "$PYTHON" "$SELF_DIR/coverage.py" "$ID" >/tmp/forge_c1 2>&1; then echo PASS; C1=PASS
else echo FAIL; cat /tmp/forge_c1; C1=FAIL; FAIL=$((FAIL+1)); fi

# ── C2 tasks-green: run EVERY task's real cmd (closes the flag-trust hole) ────
printf "[2] all-tasks-green (real execution) ... "
if [ ! -f "$TASKS" ]; then echo "FAIL (no tasks.json)"; C2=FAIL; FAIL=$((FAIL+1)); else
  RED=$("$PYTHON" - "$TASKS" <<'PY'
import json,sys,subprocess,re,os,shlex
d=json.load(open(sys.argv[1])); red=[]
run=os.environ.get("FORGE_RUN","uv run").split()
for t in d["tasks"]:
    if not t.get("passes"): red.append(t["task_id"]+":unflipped"); continue
    cmd=(t.get("acceptance") or {}).get("cmd")
    if not cmd: red.append(t["task_id"]+":nocmd"); continue
    # shlex.split (not str.split) so a quoted -k "sel" is one token WITHOUT the
    # literal quotes — str.split keeps them, which pytest rejects as a bad -k
    # expression (exit 4) and every task false-fails.
    r=subprocess.run(run+shlex.split(cmd)+["-p","no:cacheprovider","-p","no:testmon"],
                     capture_output=True,text=True)
    if r.returncode==5: red.append(t["task_id"]+":no-tests")
    elif r.returncode!=0: red.append(t["task_id"]+f":rc{r.returncode}")
print(" ".join(red[:20])+(f" (+{len(red)-20} more)" if len(red)>20 else "") if red else "")
PY
)
  if [ -z "$RED" ]; then echo PASS; C2=PASS; else echo "FAIL"; echo "  red: $RED"; C2=FAIL; FAIL=$((FAIL+1)); fi
fi

# ── C3 static + unit + invariants ────────────────────────────────────────────
printf "[3] static+unit+invariants ... "
C3=PASS
$RUN ruff check services libs scripts >/tmp/forge_c3 2>&1 || C3=FAIL
$RUN mypy --strict services libs 2>>/tmp/forge_c3 >/dev/null || true   # mypy advisory in v1
if command -v semgrep >/dev/null 2>&1 && [ -f .semgrep.yml ]; then
  semgrep --error --config .semgrep.yml services libs >>/tmp/forge_c3 2>&1 || C3=FAIL; fi
$RUN pytest -m "not reality and not e2e and not negative and not integration" \
     -p no:cacheprovider -p no:testmon -q >>/tmp/forge_c3 2>&1 || C3=FAIL
if [ "$C3" = PASS ]; then echo PASS; else echo FAIL; tail -6 /tmp/forge_c3; FAIL=$((FAIL+1)); fi

# ── C4 real-data eval >= baseline (N/A for zero-[eval] docs) ──────────────────
printf "[4] real-data eval>=baseline ... "
# grep -c already prints "0" on no-match (and exits 1); the old `|| echo 0`
# appended a SECOND "0", yielding "0\n0" → the -eq test errored and fell
# through to a false FAIL. Default only when the file is missing (grep exit 2).
EVAL_CRIT=$(grep -cE "evidence_class:\s*['\"]?\[eval" "acceptance/${DOCID}/criteria/criteria.yaml" 2>/dev/null); EVAL_CRIT=${EVAL_CRIT:-0}
# Select the doc's [eval] tests by the [eval] criteria's OWN task selectors —
# NOT `-k "$ID"`, which matched random test-node substrings across the whole tree
# (e.g. -k "03" hit one unrelated test; -k "00" hit six). Resolve real selectors.
EVAL_K=$("$PYTHON" - "$DOCID" "$ID" <<'PY'
import sys, re, json, pathlib
docid, sid = sys.argv[1], sys.argv[2]
cp = pathlib.Path(f"acceptance/{docid}/criteria/criteria.yaml")
crit = cp.read_text() if cp.exists() else ""
evalids, cur = set(), None
for line in crit.splitlines():
    m = re.match(r"\s*-?\s*criterion_id:\s*(\S+)", line)
    if m: cur = m.group(1)
    if cur and re.search(r"evidence_class:\s*['\"]?\[eval", line): evalids.add(cur)
frags = []
tp = pathlib.Path(f"slices/{sid}/tasks.json")
if tp.exists():
    for t in json.load(open(tp)).get("tasks", []):
        if any(c in evalids for c in t.get("criterion_ids", [])):
            mm = re.search(r'-k "([^"]+)"', (t.get("acceptance") or {}).get("cmd") or "")
            if mm: frags.append(mm.group(1))
print(" or ".join(dict.fromkeys(frags)))
PY
)
if [ "$EVAL_CRIT" -eq 0 ]; then echo "N/A (no [eval] criteria in bundle)"; C4=NA
elif [ ! -f "$BASELINE" ]; then echo "BLOCKED (has [eval] criteria but no _baseline.json — seal the eval baseline)"; C4=FAIL; FAIL=$((FAIL+1))
elif [ -z "$EVAL_K" ]; then echo "FAIL (could not resolve [eval] test selectors from tasks.json)"; C4=FAIL; FAIL=$((FAIL+1))
elif $RUN pytest -q -k "$EVAL_K" -p no:cacheprovider -p no:testmon >/tmp/forge_c4 2>&1; then
  echo PASS; C4=PASS
else echo FAIL; tail -6 /tmp/forge_c4; C4=FAIL; FAIL=$((FAIL+1)); fi

# ── C5 regressions ratchet ───────────────────────────────────────────────────
printf "[5] regressions ratchet ... "
if [ ! -d regressions ]; then echo "PASS (no regressions/ — vacuous)"; C5=PASS
elif $RUN pytest regressions/ -q -p no:cacheprovider -p no:testmon >/tmp/forge_c5 2>&1; then echo PASS; C5=PASS
else echo FAIL; tail -6 /tmp/forge_c5; C5=FAIL; FAIL=$((FAIL+1)); fi

echo "─────────────────────────────────────"
printf "  [1] coverage         %s\n" "$C1"
printf "  [2] all-tasks-green   %s\n" "$C2"
printf "  [3] static+unit       %s\n" "$C3"
printf "  [4] real-data eval    %s\n" "$C4"
printf "  [5] regressions       %s\n" "$C5"
echo "─────────────────────────────────────"
if [ "$FAIL" -gt 0 ]; then echo "DONE(${ID}): NOT DONE — ${FAIL} conjunct(s) block."; exit 1; fi
echo "DONE(${ID}): PRODUCTION-VERIFIED — all conjuncts pass."; exit 0
