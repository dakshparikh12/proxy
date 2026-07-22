#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Proxy verification harness — run every layer, in cheap→expensive order, for one
# doc, and write a permanent timestamped report. Reusable on ANY doc registered in
# verification/config/docs.py (doc00, doc01, doc02, and doc03+ once it lands).
#
#   ./verification/run_full_verification.sh doc02            # layers 1–4 (+6 note)
#   ./verification/run_full_verification.sh doc02 --redteam  # also Layer 5 (if eligible)
#
# Layers:
#   1 Hypothesis   property-based edge/adversarial input tests   (local, always)
#   2 DeepEval     spec-derived grounding/citation scoring        (LLM; needs credit)
#   3 PR-Agent     self-hosted Claude code review                 (Docker + LLM)
#   4 Chaos        hand-written fault injection                   (local, always)
#   5 Promptfoo    transcript prompt-injection red team           (ONLY with --redteam)
#   6 Confident AI optional cloud dashboard                       (note only)
#
# Never fabricates: a layer blocked by a missing external (LLM credit, Docker) is
# recorded BLOCKED, not passed. Reports are timestamped and never overwritten.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"          # .../verification
ROOT="$(cd "$HERE/.." && pwd)"                 # repo/worktree root
DOC="${1:-}"
REDTEAM=0
for a in "$@"; do [ "$a" = "--redteam" ] && REDTEAM=1; done

if [ -z "$DOC" ] || [ "$DOC" = "--redteam" ]; then
  echo "usage: $0 <doc> [--redteam]   (e.g. $0 doc02)"; exit 2
fi

PY="${PROXY_PY:-$ROOT/.venv/bin/python}"
TOOLS_PY="$HERE/tools/.venv/bin/python"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$HERE/reports/${DOC}-${TS}.md"
mkdir -p "$HERE/reports"

# Validate the doc is registered (also gives us its title/customer_facing flag).
INFO="$(cd "$HERE" && "$PY" -c "import sys; sys.path.insert(0,'.'); from config import docs; d=docs.get('$DOC'); print(f'{d.title}||{d.spec_path}||{int(d.customer_facing)}')" 2>/dev/null)"
if [ -z "$INFO" ]; then echo "unknown doc '$DOC' (not in verification/config/docs.py)"; exit 2; fi
TITLE="${INFO%%||*}"; REST="${INFO#*||}"; SPEC="${REST%%||*}"; CUSTOMER_FACING="${REST##*||}"

# ── report header ────────────────────────────────────────────────────────────
{
echo "# Verification report — ${DOC} (${TITLE})"
echo
echo "- **Timestamp (UTC):** ${TS}"
echo "- **Spec:** \`${SPEC}\`"
echo "- **Commit:** \`$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo n/a)\` on \`$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo n/a)\`"
echo "- **Red team (Layer 5):** $([ $REDTEAM -eq 1 ] && echo 'requested' || echo 'not requested (default)')"
echo
echo "| Layer | Status | Evidence |"
echo "|-------|--------|----------|"
} > "$REPORT"

FAILED=0; BLOCKED=0
row() { echo "| $1 | $2 | $3 |" >> "$REPORT"; }

# ── Layer 1 — Hypothesis ─────────────────────────────────────────────────────
L1_OUT="$(cd "$HERE" && HYPOTHESIS_PROFILE="${HYPOTHESIS_PROFILE:-ci}" "$PY" -m pytest "scenarios/$DOC/hypothesis_props.py" -q 2>&1)"
L1_RC=$?
L1_SUM="$(printf '%s\n' "$L1_OUT" | grep -oE '[0-9]+ (passed|failed)([, ].*)?' | tail -1)"
if [ $L1_RC -eq 0 ]; then row "1 Hypothesis" "✅ PASS" "${L1_SUM:-passed}"; else row "1 Hypothesis" "❌ FAIL" "${L1_SUM:-see log}"; FAILED=1; fi

# ── Layer 2 — DeepEval ───────────────────────────────────────────────────────
if [ -x "$TOOLS_PY" ]; then
  L2_OUT="$(cd "$HERE" && "$TOOLS_PY" config/layer2_deepeval.py "$DOC" 2>&1)"
  L2_JSON="$HERE/scenarios/$DOC/deepeval_results.json"
  if [ -f "$L2_JSON" ]; then
    L2_VERD="$("$PY" -c "import json;d=json.load(open('$L2_JSON'));print('BLOCKED' if d.get('blocked') else ('PASS' if d.get('passed') else 'FAIL'))" 2>/dev/null)"
    L2_DET="$("$PY" -c "import json;d=json.load(open('$L2_JSON'));print(d.get('blocked_reason') or f\"grounded {d.get('grounded_pass')}/{d.get('grounded_total')}, neg-ctl caught {d.get('negative_controls_caught')}/{d.get('negative_controls_total')}\")" 2>/dev/null)"
    case "$L2_VERD" in
      PASS) row "2 DeepEval" "✅ PASS" "$L2_DET";;
      BLOCKED) row "2 DeepEval" "⚠️ BLOCKED" "$(echo "$L2_DET" | head -c 120)"; BLOCKED=1;;
      *) row "2 DeepEval" "❌ FAIL" "$L2_DET"; FAILED=1;;
    esac
  else row "2 DeepEval" "⚠️ BLOCKED" "no results written"; BLOCKED=1; fi
else
  row "2 DeepEval" "⚠️ SKIP" "tool env missing — run: (cd verification/tools && uv sync)"; BLOCKED=1
fi

# ── Layer 3 — PR-Agent ───────────────────────────────────────────────────────
if docker info >/dev/null 2>&1; then
  row "3 PR-Agent" "⚠️ MANUAL" "Docker up — run verification/config/pr_agent/run_pr_agent.sh <PR_URL> (needs funded key)"
else
  row "3 PR-Agent" "⚠️ BLOCKED" "Docker daemon down; config at verification/config/pr_agent/"
fi
BLOCKED=1

# ── Layer 4 — Chaos ──────────────────────────────────────────────────────────
if [ -f "$HERE/chaos/$DOC.py" ]; then
  L4_OUT="$("$PY" "$HERE/chaos/$DOC.py" 2>&1)"
  L4_SURV="$(printf '%s' "$L4_OUT" | "$PY" -c "import sys,json;
try:
  d=json.load(sys.stdin); print('SURV' if d.get('skipped') is False and d.get('survived') else ('SKIP' if d.get('skipped') else 'FAIL')); print(d.get('detail') or d.get('skip_reason'))
except Exception as e: print('FAIL'); print('unparseable: '+str(e)[:80])" 2>/dev/null)"
  L4_V="$(printf '%s\n' "$L4_SURV" | head -1)"; L4_D="$(printf '%s\n' "$L4_SURV" | tail -1)"
  case "$L4_V" in
    SURV) row "4 Chaos" "✅ SURVIVED" "$L4_D";;
    SKIP) row "4 Chaos" "⚠️ SKIP" "$L4_D"; BLOCKED=1;;
    *) row "4 Chaos" "❌ FAIL" "$L4_D"; FAILED=1;;
  esac
else row "4 Chaos" "— none" "no chaos/$DOC.py"; fi

# ── Layer 5 — Promptfoo (gated) ──────────────────────────────────────────────
if [ $REDTEAM -eq 1 ] && [ "$CUSTOMER_FACING" = "1" ]; then
  if command -v npx >/dev/null 2>&1; then
    # Ungated hand-authored injection tests (auto-gen variants are email-gated —
    # see redteam/promptfoo-autogen.yaml).
    L5_OUT="$(cd "$HERE" && PROMPTFOO_DISABLE_TELEMETRY=1 npx --yes promptfoo@latest eval \
      -c redteam/promptfooconfig.yaml --no-cache -o "reports/${DOC}-${TS}-promptfoo.json" 2>&1)"
    L5_PF="$(printf '%s' "$L5_OUT" | sed -E 's/\x1b\[[0-9;]*m//g' | grep -oE '[0-9]+ passed \([0-9]+%\)' | tail -1)"
    if printf '%s' "$L5_OUT" | grep -q '0 failed'; then row "5 Promptfoo" "✅ PASS" "${L5_PF:-passed}, results reports/${DOC}-${TS}-promptfoo.json";
    else row "5 Promptfoo" "❌ see log" "${L5_PF:-review output}"; FAILED=1; fi
  else row "5 Promptfoo" "⚠️ SKIP" "npx not available"; fi
elif [ $REDTEAM -eq 1 ]; then
  row "5 Promptfoo" "— n/a" "$DOC is not customer_facing; red team not applicable yet"
else
  row "5 Promptfoo" "⏸ gated" "not run by default; pass --redteam (built at verification/redteam/)"
fi

# ── Layer 6 — Confident AI ───────────────────────────────────────────────────
row "6 Confident AI" "ⓘ optional" "free tier needs an account/API key; wire with 'deepeval login' — deferred"

# ── verdict ──────────────────────────────────────────────────────────────────
if [ $FAILED -eq 1 ]; then VERDICT="NOT READY — a layer FAILED (see above)";
elif [ $BLOCKED -eq 1 ]; then VERDICT="LAYERS GREEN, WITH CAVEATS — executed layers passed; some layers blocked by external limits (LLM credit / Docker). Final customer-readiness also requires the sealed acceptance gate (harness/verify.sh) green.";
else VERDICT="LAYERS GREEN — all executed layers passed."; fi

{
echo
echo "## Verdict"
echo
echo "**${VERDICT}**"
echo
echo "> This machine report covers the verification LAYERS only. The curated"
echo "> per-doc report (same folder) records defects found & fixed and the final"
echo "> READY / NOT READY reasoning integrating the sealed acceptance gate."
} >> "$REPORT"

echo "report: $REPORT"
[ $FAILED -eq 1 ] && exit 1 || exit 0
