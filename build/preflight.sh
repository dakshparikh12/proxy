#!/usr/bin/env bash
# preflight — Phase 0 env gate. Hard-codes every environment killer we have ALREADY hit
# so it can NEVER recur silently and NO phase time is spent fighting config. Exit 0 iff the
# environment is in the known-good state. Run before Phase 1 and again before Phase 2, and
# after any interruption. This is deterministic; a red here is a real env fault, not a phase.
#
# The killers this asserts against (each cost real time before it was root-caused):
#   - iCloud venv corruption   -> .venv MUST be a symlink OFF the iCloud-synced repo path
#   - ModuleNotFoundError: db  -> core workspace packages MUST import (right python version)
#   - testmon cold-start freeze -> pyproject MUST disable testmon globally
#   - silent gate false-pass   -> the fail-closed gates MUST be present + executable
#   - config drift mid-run     -> pyproject + lock + settings are frozen; a change is a HALT
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
FAIL=0
pass() { printf "  [PASS] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1"; FAIL=$((FAIL+1)); }

echo "== preflight (Phase 0 env gate) =="

# 0) repo + venv exist — clear errors instead of confusing downstream failures
[ -d .git ] || { echo "  [FAIL] not at a git repo root (.git missing) — run from repo root"; exit 1; }
[ -e .venv ] || { echo "  [FAIL] .venv missing — rebuild: uv sync --all-packages (venv off iCloud)"; exit 1; }

# 1) venv OFF iCloud — must be a symlink whose target is NOT under the synced repo/Library path
VENV_TGT="$(readlink .venv || true)"
if [ -z "$VENV_TGT" ]; then fail ".venv is not a symlink (iCloud will corrupt an in-repo venv)"
elif echo "$VENV_TGT" | grep -qE "Mobile Documents|/Desktop/|iCloud"; then
  fail ".venv target is on an iCloud-synced path ($VENV_TGT) — move it off (see venv-restore)"
else pass ".venv -> $VENV_TGT (off iCloud)"; fi

# 2) core workspace packages import (the db canary) + python >= 3.12 (pyproject constraint)
if .venv/bin/python -c "import contracts, agentkit, db, http" 2>/dev/null; then
  pass "core workspace imports (contracts, agentkit, db, http)"
else fail "core workspace import broken — rebuild venv (uv sync --all-packages, never bare)"; fi
PYV="$(.venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)"
PYOK="$(.venv/bin/python -c 'import sys;print(1 if sys.version_info[:2]>=(3,12) else 0)' 2>/dev/null || echo 0)"
[ "$PYOK" = "1" ] && pass "python $PYV (>=3.12)" || fail "python '$PYV' does not satisfy pyproject >=3.12 — rebuild venv"

# 3) testmon disabled globally (the ~19min freeze)
if grep -q "no:testmon" pyproject.toml; then pass "testmon disabled in pyproject addopts"
else fail "pyproject addopts missing '-p no:testmon' — full-suite runs will freeze"; fi

# 4) fail-closed gates present + runnable (self-contained; no forge/ or done-check dependency)
[ -f build/check_completeness.py ] && pass "check_completeness.py present" || fail "build/check_completeness.py missing"
[ -x build/gates/verify-node.sh ] && pass "verify-node.sh present" || fail "build/gates/verify-node.sh missing/not executable"
[ -x build/gates/signoff.sh ]     && pass "signoff.sh present"     || fail "build/gates/signoff.sh missing/not executable"

# 5) read-only guard present as a REFERENCE (not wired — enforcement is the fresh verifier; see SPEC §9)
[ -f .claude/hooks/pretool_guard.py ] && pass "read-only guard present (reference)" || pass "read-only guard absent (non-blocking — verifier enforces)"

# 6) config-freeze fingerprint — record on first run; on later runs, a change is a HALT
STAMP=".git/proxy-build-config.sha"
CUR="$(shasum pyproject.toml uv.lock .claude/settings.json 2>/dev/null | shasum | cut -d' ' -f1)"
if [ -f "$STAMP" ]; then
  if [ "$(cat "$STAMP")" = "$CUR" ]; then pass "config frozen (pyproject + uv.lock + settings unchanged)"
  else fail "CONFIG DRIFT: pyproject/uv.lock/settings changed mid-run — HALT and review (config is frozen)"; fi
else echo "$CUR" > "$STAMP"; pass "config fingerprint recorded (frozen from here)"; fi

# 7) observability (WARN only — keys come from Secret Manager; the lead sources them per run)
if [ -n "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" ] && [ -n "${CLAUDE_CODE_ENABLE_TELEMETRY:-}" ]; then
  pass "observability env set (telemetry on)"
else echo "  [WARN] observability env not set — source observability.md §1 before running (not a hard block)"; fi

echo "────────────────────────────────────"
if [ "$FAIL" -gt 0 ]; then echo "preflight: NOT READY — ${FAIL} env fault(s). Fix the environment, do NOT start a phase."; exit 1; fi
echo "preflight: READY — environment hardened; all time now goes to the phases."; exit 0
