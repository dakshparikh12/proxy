#!/usr/bin/env bash
# drive.sh — v2 build-loop orchestrator for a spec slice
#
# Usage: ./drive.sh <id>
#
# Sequences + gates:
#   1. mkdir slices/<id>
#   2. If no tasks.json → decompose
#   3. coverage_gate (fail fast on gap)
#   4. task_coverage  (fail fast on gap)
#   5. print task count
#   6. done-check.sh --spec <id> → print DONE or blocked conjunct list
#   7. Cost ceiling check via scripts/cost_log.py
#
# Environment:
#   V2_COST_CEILING_USD   (default: 25) — abort if spent_usd exceeds this
#
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <id>  (e.g. 00 or 03)" >&2
    exit 1
fi

ID="$1"
DOCID="doc${ID}"
SLICE_DIR="slices/${ID}"
TASKS_JSON="${SLICE_DIR}/tasks.json"

echo "=== drive.sh ${ID} ==="
echo ""

# ── Step 1: mkdir slices/<id> ────────────────────────────────────────────────
mkdir -p "${SLICE_DIR}"

# ── Step 2: decompose if no tasks.json ──────────────────────────────────────
if [[ ! -f "${TASKS_JSON}" ]]; then
    echo "[drive] tasks.json missing — running decompose..."
    # A decompose that HALTs on EXTRACTION_COUNT prints and exits 1 — surface it
    if ! python3 scripts/decompose.py "${ID}"; then
        echo ""
        echo "BLOCKED: decompose failed for ${ID} (check EXTRACTION_COUNT_HALT above)" >&2
        exit 1
    fi
fi

# ── Step 3: coverage_gate ────────────────────────────────────────────────────
echo "[drive] coverage_gate..."
if ! python3 scripts/coverage_gate.py "${DOCID}"; then
    echo ""
    echo "BLOCKED: coverage_gate found gaps in ${DOCID}." >&2
    echo "  Founder-fix: seal the acceptance bundle before driving this spec." >&2
    exit 1
fi

# ── Step 4: task_coverage ────────────────────────────────────────────────────
echo ""
echo "[drive] task_coverage..."
if ! python3 scripts/task_coverage.py "${ID}"; then
    echo ""
    echo "BLOCKED: task_coverage found gaps in ${ID}." >&2
    echo "  Founder-fix: run decompose again or add missing task assignments." >&2
    exit 1
fi

# ── Step 5: print task count ──────────────────────────────────────────────────
TASK_COUNT=$(python3 - <<EOF
import json
data = json.loads(open("${TASKS_JSON}").read())
print(len(data.get("tasks", [])))
EOF
)
echo ""
echo "[drive] task count: ${TASK_COUNT} tasks in slices/${ID}/tasks.json"

# ── Step 6: cost ceiling check ───────────────────────────────────────────────
COST_CEILING="${V2_COST_CEILING_USD:-25}"
if SPENT=$(python3 scripts/cost_log.py --query-spent "${ID}" 2>/dev/null); then
    echo ""
    echo "[drive] cost: \$${SPENT} spent of \$${COST_CEILING} ceiling"
    OVER_CEILING=$(python3 -c "print('yes' if float('${SPENT}') > float('${COST_CEILING}') else 'no')")
    if [[ "$OVER_CEILING" == "yes" ]]; then
        echo ""
        echo "BLOCKED:cost-ceiling — \$${SPENT} exceeds V2_COST_CEILING_USD=\$${COST_CEILING}" >&2
        exit 1
    fi
else
    echo ""
    echo "[drive] cost: untracked (cost_log unavailable; ceiling ${COST_CEILING} USD not enforced)"
fi

# ── Step 7: done-check.sh --spec <id> ────────────────────────────────────────
echo ""
echo "[drive] running done-check.sh --spec ${ID}..."
echo ""
if ./done-check.sh --spec "${ID}"; then
    echo ""
    echo "[drive] DONE(${ID}): ALL CONJUNCTS PASS — spec is DONE"
    exit 0
else
    echo ""
    echo "[drive] NOT DONE(${ID}) — see blocked conjuncts above"
    exit 1
fi
