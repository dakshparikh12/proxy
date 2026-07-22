#!/usr/bin/env bash
# done-check.sh — v2 build-loop DONE predicate
#
# Usage:
#   ./done-check.sh --task <id> <task_id>   exit 0 iff task's acceptance.cmd is green AND passes:true
#   ./done-check.sh --spec <id>             print per-conjunct table; exit 0 iff ALL hold
#
# set -uo pipefail; cd to repo root.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

usage() {
    echo "Usage: $0 --task <id> <task_id>" >&2
    echo "       $0 --spec <id>" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

MODE="$1"
shift

# ─────────────────────────────────────────────
# --task <id> <task_id>
# ─────────────────────────────────────────────
if [[ "$MODE" == "--task" ]]; then
    if [[ $# -lt 2 ]]; then
        echo "Usage: $0 --task <id> <task_id>" >&2
        exit 1
    fi
    ID="$1"
    TASK_ID="$2"

    TASKS_JSON="slices/${ID}/tasks.json"
    if [[ ! -f "$TASKS_JSON" ]]; then
        echo "ERROR: $TASKS_JSON not found — run decompose first" >&2
        exit 1
    fi

    # Read acceptance.cmd and passes from tasks.json
    CMD=$(python3 - <<EOF
import json, sys
data = json.loads(open("${TASKS_JSON}").read())
task = next((t for t in data["tasks"] if t["task_id"] == "${TASK_ID}"), None)
if task is None:
    print("")
    sys.exit(2)
cmd = task.get("acceptance", {}).get("cmd")
print(cmd if cmd else "")
EOF
)
    PY_RC=$?
    if [[ $PY_RC -eq 2 ]]; then
        echo "ERROR: task ${TASK_ID} not found in ${TASKS_JSON}" >&2
        exit 1
    fi

    PASSES=$(python3 - <<EOF
import json
data = json.loads(open("${TASKS_JSON}").read())
task = next((t for t in data["tasks"] if t["task_id"] == "${TASK_ID}"), None)
print("true" if task and task.get("passes") else "false")
EOF
)

    if [[ -z "$CMD" ]]; then
        echo "BLOCKED: ${TASK_ID} has no acceptance cmd"
        exit 1
    fi

    if [[ "$PASSES" != "true" ]]; then
        echo "FAIL: ${TASK_ID} passes:false in tasks.json"
        exit 1
    fi

    # Run the acceptance cmd — append -p no:cacheprovider
    echo "Running: uv run $CMD -p no:cacheprovider"
    # shellcheck disable=SC2086
    uv run $CMD -p no:cacheprovider
    CMD_RC=$?

    if [[ $CMD_RC -eq 5 ]]; then
        echo "FAIL: ${TASK_ID} acceptance cmd collected no tests (pytest exit 5 = no tests selected)"
        exit 1
    fi

    if [[ $CMD_RC -ne 0 ]]; then
        echo "FAIL: ${TASK_ID} acceptance cmd exited $CMD_RC"
        exit 1
    fi

    echo "PASS: ${TASK_ID}"
    exit 0
fi

# ─────────────────────────────────────────────
# --spec <id>
# ─────────────────────────────────────────────
if [[ "$MODE" != "--spec" ]]; then
    usage
fi

ID="$1"
DOCID="doc${ID}"
TASKS_JSON="slices/${ID}/tasks.json"
BASELINE_JSON="slices/${ID}/_baseline.json"

echo "== DONE(${ID}) =="
echo ""

OVERALL_FAIL=0    # count of FAIL conjuncts
OVERALL_DEFERRED=0  # count of DEFERRED conjuncts

# Per-conjunct status strings: C1_STATUS, C2_STATUS, ..., C10_STATUS
# Each is one of: PASS | FAIL | DEFERRED
# DEFERRED is a distinct third state — it blocks DONE just like FAIL.
C1_STATUS="UNKNOWN"
C2_STATUS="UNKNOWN"
C3_STATUS="UNKNOWN"
C4_STATUS="UNKNOWN"
C5_STATUS="UNKNOWN"
C6_STATUS="UNKNOWN"
C7_STATUS="UNKNOWN"
C8_STATUS="UNKNOWN"
C9_STATUS="UNKNOWN"
C10_STATUS="UNKNOWN"

# ── Conjunct 1: coverage(req↔crit) ──────────────────────────────────────────
echo -n "[1] coverage(req<->crit) ... "
if python3 scripts/coverage_gate.py "${DOCID}" > /tmp/done_check_c1.txt 2>&1; then
    echo "PASS"
    C1_STATUS="PASS"
else
    echo "FAIL"
    cat /tmp/done_check_c1.txt
    C1_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
fi

# ── Conjunct 2: tasks(crit↔task) ────────────────────────────────────────────
echo -n "[2] tasks(crit<->task) ... "
if python3 scripts/task_coverage.py "${ID}" > /tmp/done_check_c2.txt 2>&1; then
    echo "PASS"
    C2_STATUS="PASS"
else
    echo "FAIL"
    cat /tmp/done_check_c2.txt
    C2_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
fi

# ── Conjunct 3: all-tasks-pass ───────────────────────────────────────────────
echo -n "[3] all-tasks-pass ... "
if [[ ! -f "$TASKS_JSON" ]]; then
    echo "FAIL (no tasks.json)"
    C3_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
else
    ALL_PASS=$(python3 - <<EOF
import json
data = json.loads(open("${TASKS_JSON}").read())
tasks = data.get("tasks", [])
if not tasks:
    print("empty")
elif all(t.get("passes") for t in tasks):
    print("true")
else:
    false_count = sum(1 for t in tasks if not t.get("passes"))
    print(f"false:{false_count}/{len(tasks)}")
EOF
)
    if [[ "$ALL_PASS" == "true" ]]; then
        echo "PASS"
        C3_STATUS="PASS"
    elif [[ "$ALL_PASS" == "empty" ]]; then
        echo "FAIL (tasks list is empty)"
        C3_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    else
        echo "FAIL (${ALL_PASS} tasks have passes:false)"
        C3_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    fi
fi

# ── Conjunct 4: offline suite + ruff ─────────────────────────────────────────
# -p no:testmon: avoid testmon lock-file conflict if done-check is invoked
#   while another pytest session is running (e.g. from CI or a nested call).
echo -n "[4] offline suite + ruff ... "
OFFLINE_FAIL=0
if ! uv run pytest -m "not reality and not e2e and not negative and not integration" -p no:cacheprovider -p no:testmon -q > /tmp/done_check_c4_pytest.txt 2>&1; then
    OFFLINE_FAIL=1
fi
if ! uv run ruff check services libs scripts > /tmp/done_check_c4_ruff.txt 2>&1; then
    OFFLINE_FAIL=1
fi
if [[ $OFFLINE_FAIL -eq 0 ]]; then
    echo "PASS"
    C4_STATUS="PASS"
else
    echo "FAIL"
    [[ -s /tmp/done_check_c4_pytest.txt ]] && tail -5 /tmp/done_check_c4_pytest.txt
    [[ -s /tmp/done_check_c4_ruff.txt ]] && cat /tmp/done_check_c4_ruff.txt
    C4_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
fi

# ── Conjunct 5: integration(Doc09 §2) ────────────────────────────────────────
echo -n "[5] integration(Doc09§2) ... "
echo "(note: needs live Postgres/GCS for persistence check; DEFERRED lines from journey = not-yet-PASS)"
if python3 scripts/journey.py contracts > /tmp/done_check_c5.txt 2>&1; then
    echo "[5] PASS"
    C5_STATUS="PASS"
else
    echo "[5] FAIL"
    cat /tmp/done_check_c5.txt
    C5_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
fi

# ── Conjunct 6: invariants ───────────────────────────────────────────────────
# Path used: try pre-commit first; if unavailable, try fallback ops.* modules;
# if ops module is also absent, DEFERRED (logged honestly; never silently PASS).
# DEFERRED is not PASS — it blocks DONE like any FAIL.
echo -n "[6] invariants ... "
if uv run pre-commit run --all-files > /tmp/done_check_c6.txt 2>&1; then
    echo "PASS (via pre-commit)"
    C6_STATUS="PASS"
else
    PRE_COMMIT_RC=$?
    # pre-commit not installed or failed — try fallback
    if [[ $PRE_COMMIT_RC -eq 127 ]] || grep -q "No such file or directory" /tmp/done_check_c6.txt 2>/dev/null || grep -q "Failed to spawn" /tmp/done_check_c6.txt 2>/dev/null; then
        echo "(pre-commit not available; trying fallback)"
        # Check if ops module is available for the fallback
        if uv run python -c "import ops" > /dev/null 2>&1; then
            # ops module available — run full fallback
            C6_FAIL=0
            if ! uv run python -m ops.check_secret_bindings > /tmp/done_check_c6_secrets.txt 2>&1; then
                echo "  FAIL: check_secret_bindings"
                cat /tmp/done_check_c6_secrets.txt
                C6_FAIL=1
            fi
            if ! uv run python -m ops.check_sdk_isolation_triad > /tmp/done_check_c6_triad.txt 2>&1; then
                echo "  FAIL: check_sdk_isolation_triad"
                cat /tmp/done_check_c6_triad.txt
                C6_FAIL=1
            fi
            if ! uv run pytest tests/doc00/test_m12_con.py -q -p no:cacheprovider -p no:testmon > /tmp/done_check_c6_naming.txt 2>&1; then
                echo "  FAIL: naming lint (test_m12_con)"
                cat /tmp/done_check_c6_naming.txt
                C6_FAIL=1
            fi
            if [[ $C6_FAIL -eq 0 ]]; then
                echo "[6] PASS (fallback: check_secret_bindings + check_sdk_isolation_triad + naming)"
                C6_STATUS="PASS"
            else
                echo "[6] FAIL (fallback)"
                C6_STATUS="FAIL"
                OVERALL_FAIL=$((OVERALL_FAIL + 1))
            fi
        else
            # Neither pre-commit nor ops module available — DEFERRED
            # DEFERRED is not PASS: it blocks DONE just like FAIL.
            echo "[6] DEFERRED (pre-commit not installed; ops module not available for fallback)"
            echo "    Install pre-commit to enable invariant checks."
            C6_STATUS="DEFERRED"
            OVERALL_DEFERRED=$((OVERALL_DEFERRED + 1))
        fi
    else
        echo "FAIL (pre-commit exited $PRE_COMMIT_RC)"
        cat /tmp/done_check_c6.txt
        C6_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    fi
fi

# ── Conjunct 7: real-data eval ≥ baseline ────────────────────────────────────
echo -n "[7] eval>=baseline ... "
if [[ ! -f "$BASELINE_JSON" ]]; then
    echo "FAIL"
    echo "  eval: NO BASELINE (blocked) — create slices/${ID}/_baseline.json to unlock"
    C7_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
else
    # Baseline exists — run eval suite
    if uv run --group eval pytest tests/eval -q -k "${ID}" > /tmp/done_check_c7.txt 2>&1; then
        echo "PASS"
        C7_STATUS="PASS"
    else
        echo "FAIL"
        tail -10 /tmp/done_check_c7.txt
        C7_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    fi
fi

# ── Conjunct 8: mutation spot-check ─────────────────────────────────────────
# Guards that acceptance cmds actually bind to real passing tests.
# Algorithm:
#   1. Pick the first task with a non-null acceptance.cmd.
#   2. Run the ORIGINAL cmd; assert it exits 0 (real green, not exit 5 = no tests).
#   3. Run the MUTATED cmd (impossible -k suffix); assert nonzero.
#   Both must hold for PASS. If no runnable cmd exists yet, DEFERRED (blocks DONE).
echo -n "[8] mutation-spotcheck ... "
if [[ ! -f "$TASKS_JSON" ]]; then
    # No tasks.json at all — DEFERRED (blocks DONE)
    echo "DEFERRED (no tasks.json)"
    C8_STATUS="DEFERRED"
    OVERALL_DEFERRED=$((OVERALL_DEFERRED + 1))
else
    REAL_CMD=$(python3 - <<EOF
import json
data = json.loads(open("${TASKS_JSON}").read())
for t in data["tasks"]:
    cmd = t.get("acceptance", {}).get("cmd")
    if cmd:
        print(cmd)
        break
EOF
)
    if [[ -z "$REAL_CMD" ]]; then
        # No task has a real acceptance.cmd yet — DEFERRED (blocks DONE)
        echo "DEFERRED (no task with real acceptance.cmd — nothing built yet)"
        C8_STATUS="DEFERRED"
        OVERALL_DEFERRED=$((OVERALL_DEFERRED + 1))
    else
        # Step 1: run the ORIGINAL cmd and assert it exits 0 (real green)
        # shellcheck disable=SC2086
        if ! uv run $REAL_CMD -p no:cacheprovider -p no:testmon > /tmp/done_check_c8_orig.txt 2>&1; then
            ORIG_RC=$?
            if [[ $ORIG_RC -eq 5 ]]; then
                echo "FAIL (original cmd collected no tests — exit 5; acceptance cmd does not bind to real tests)"
            else
                echo "FAIL (original cmd exited $ORIG_RC — acceptance cmd is not green)"
            fi
            C8_STATUS="FAIL"
            OVERALL_FAIL=$((OVERALL_FAIL + 1))
        else
            # Step 2: run the MUTATED cmd and assert nonzero
            MUTATED_CMD="${REAL_CMD} -k NOPE_MUTATION_SPOTCHECK_XYZ_IMPOSSIBLE"
            # shellcheck disable=SC2086
            if uv run $MUTATED_CMD -p no:cacheprovider -p no:testmon > /tmp/done_check_c8.txt 2>&1; then
                echo "FAIL (mutated cmd passed — acceptance cmd does not truly bind)"
                C8_STATUS="FAIL"
                OVERALL_FAIL=$((OVERALL_FAIL + 1))
            else
                echo "PASS (original green; mutated cmd correctly returned nonzero)"
                C8_STATUS="PASS"
            fi
        fi
    fi
fi

# ── Conjunct 9: regressions ratchet ─────────────────────────────────────────
echo -n "[9] regressions ratchet ... "
if [[ ! -d "regressions" ]]; then
    echo "PASS (no regressions/ dir — vacuously green)"
    C9_STATUS="PASS"
elif uv run pytest regressions/ -q -p no:cacheprovider > /tmp/done_check_c9.txt 2>&1; then
    echo "PASS"
    C9_STATUS="PASS"
else
    echo "FAIL"
    tail -5 /tmp/done_check_c9.txt
    C9_STATUS="FAIL"
    OVERALL_FAIL=$((OVERALL_FAIL + 1))
fi

# ── Conjunct 10: pip-audit ───────────────────────────────────────────────────
# CRITICAL: if pip-audit output contains actual CVE findings (GHSA-/PYSEC-/vulnerability),
# conjunct 10 is FAIL regardless of "could not be audited" notes for workspace-local packages.
# DEFERRED only when the ONLY issue is inaccessible local packages AND no CVE was found.
echo -n "[10] mechanical(pip-audit) ... "
if uv run pip-audit > /tmp/done_check_c10.txt 2>&1; then
    echo "PASS"
    C10_STATUS="PASS"
else
    PIP_AUDIT_RC=$?
    PIP_AUDIT_OUT=$(cat /tmp/done_check_c10.txt)
    # Check for actual CVE findings FIRST — these are always FAIL, regardless of other notes
    if echo "$PIP_AUDIT_OUT" | grep -qiE "GHSA-|PYSEC-|vulnerabilit"; then
        echo "FAIL (pip-audit found real CVE/vulnerability findings)"
        tail -10 /tmp/done_check_c10.txt
        C10_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    elif echo "$PIP_AUDIT_OUT" | grep -qiE "network|connection|timeout|offline|unreachable"; then
        echo "DEFERRED (pip-audit needs network — not available in this environment)"
        C10_STATUS="DEFERRED"
        OVERALL_DEFERRED=$((OVERALL_DEFERRED + 1))
    elif echo "$PIP_AUDIT_OUT" | grep -q "could not be audited"; then
        # Workspace-local packages (not on PyPI) can't be audited, but no CVEs found.
        echo "DEFERRED (pip-audit cannot audit workspace-local packages not on PyPI; no CVEs found in auditable packages)"
        tail -5 /tmp/done_check_c10.txt
        C10_STATUS="DEFERRED"
        OVERALL_DEFERRED=$((OVERALL_DEFERRED + 1))
    else
        echo "FAIL (pip-audit exited $PIP_AUDIT_RC)"
        tail -5 /tmp/done_check_c10.txt
        C10_STATUS="FAIL"
        OVERALL_FAIL=$((OVERALL_FAIL + 1))
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────"
echo "DONE(${ID}) SUMMARY"
echo "─────────────────────────────────────────────────"
printf "  [%2d] %-30s %s\n"  "1" "coverage(req<->crit)"    "$C1_STATUS"
printf "  [%2d] %-30s %s\n"  "2" "tasks(crit<->task)"      "$C2_STATUS"
printf "  [%2d] %-30s %s\n"  "3" "all-tasks-pass"           "$C3_STATUS"
printf "  [%2d] %-30s %s\n"  "4" "offline suite + ruff"     "$C4_STATUS"
printf "  [%2d] %-30s %s\n"  "5" "integration(Doc09§2)"    "$C5_STATUS"
printf "  [%2d] %-30s %s\n"  "6" "invariants"               "$C6_STATUS"
printf "  [%2d] %-30s %s\n"  "7" "eval>=baseline"           "$C7_STATUS"
printf "  [%2d] %-30s %s\n"  "8" "mutation-spotcheck"       "$C8_STATUS"
printf "  [%2d] %-30s %s\n"  "9" "regressions ratchet"      "$C9_STATUS"
printf "  [%2d] %-30s %s\n" "10" "mechanical(pip-audit)"    "$C10_STATUS"
echo "─────────────────────────────────────────────────"

if [[ $OVERALL_FAIL -gt 0 ]] || [[ $OVERALL_DEFERRED -gt 0 ]]; then
    BLOCKING=""
    if [[ $OVERALL_FAIL -gt 0 ]]; then
        BLOCKING="${OVERALL_FAIL} FAIL"
    fi
    if [[ $OVERALL_DEFERRED -gt 0 ]]; then
        if [[ -n "$BLOCKING" ]]; then
            BLOCKING="${BLOCKING}, ${OVERALL_DEFERRED} DEFERRED"
        else
            BLOCKING="${OVERALL_DEFERRED} DEFERRED"
        fi
    fi
    echo "DONE(${ID}): NOT DONE — ${BLOCKING} conjunct(s) block completion (see table above)"
    exit 1
else
    echo "DONE(${ID}): ALL CONJUNCTS PASS"
    exit 0
fi
