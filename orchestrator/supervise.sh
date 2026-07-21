#!/usr/bin/env bash
# SUPERVISOR — keep the autonomous build going until the whole product is done.
# Restarts the conductor from the first unfinished doc whenever it exits (plumbing halt, crash,
# transient), because orchestrate.py resumes instantly past any sealed/built work. Bounded so it
# can NEVER spin forever, and it never fails SILENTLY: every halt (content-stuck, genuine auth, or a
# transient network outage) is written LOUDLY to BOTH stdout (→console.log) AND orchestrator/run.log
# — the founder's canonical log — with the real underlying reason, then exits.
#
# One-command launch (starts this + the watchdog, detached, sleep-proof): orchestrator/launch.sh
#   Legacy manual form (kept working): caffeinate -dimsu bash orchestrator/supervise.sh 2>&1 | tee -a orchestrator/console.log
# Start from a specific doc (honors an operator's `--from`): pass it as the first arg (or START_DOC=):
#   bash orchestrator/supervise.sh doc03
#   The supervisor then never re-selects a doc BEFORE the floor. (It otherwise picks the first
#   doc lacking a <doc>-done tag, which is why a `--from doc02` typed at the shell was ignored and
#   every restart rebuilt doc00.) The conductor also skips any doc already sealed+verified.
# Stop it cleanly:  touch orchestrator/STOP
set -uo pipefail
cd "$(dirname "$0")/.."
DOCS=(doc00 doc01 doc02 doc03 doc04 doc05 doc08 doc09)
MAX_RESTARTS="${MAX_RESTARTS:-60}"
STUCK_LIMIT="${STUCK_LIMIT:-3}"     # same doc, no NEW commits, N restarts in a row => halt loudly.
NET_LIMIT="${NET_LIMIT:-5}"         # consecutive TRANSIENT preflight (rc=4) failures => pause loudly.
NET_BACKOFF="${NET_BACKOFF:-60}"    # per-failure backoff seconds (grows linearly, capped 600). Test knob.
RUNLOG="orchestrator/run.log"
#   The conductor now bounds every subprocess (claude phases + local tools) and kills the whole
#   process group on timeout, so a "stuck" doc is a genuine content/spec blocker — NOT a technical
#   hang — worth a couple of clean retries (resume skips sealed work) and then a human.
# Optional operator start-floor (honors `--from`): `supervise.sh doc02` or START_DOC=doc02.
# The supervisor never selects a doc before this floor. Default doc00 = original behavior.
START_FLOOR="${1:-${START_DOC:-doc00}}"
if ! printf '%s\n' "${DOCS[@]}" | grep -qx "$START_FLOOR"; then
  echo "[supervisor] START_FLOOR '$START_FLOOR' is not a known doc (${DOCS[*]}) — refusing to guess. Exiting."
  exit 2
fi
i=0; last=""; stuck=0; net_fail=0; last_rc=0

# A halt line that lands in BOTH the live console AND run.log (the founder reads run.log first). The
# old halts printed to stdout only, so run.log showed a bare stack of PATH-augment lines with no why.
runlog() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >> "$RUNLOG"; }
both()   { echo "$*"; runlog "$*"; }
# Surface the REAL underlying reason (preflight fail / phase result) from the logs into the banner.
last_reason() {
  grep -hE "PRELAUNCH FAIL|==> |TIMEOUT|LADDER_RED|EXTRACTION_COUNT_HALT|COVERAGE_GATE" \
       "$RUNLOG" orchestrator/console.log 2>/dev/null | tail -6
}

# Pin this run to the SHA we launched at: a founder push can't silently mutate a live run.
# The supervisor only fast-forwards new work at a doc boundary (ALLOW_PULL present), never mid-doc.
mkdir -p orchestrator/state
git rev-parse HEAD > orchestrator/state/run.sha 2>/dev/null || true
echo "[supervisor] launch SHA $(cut -c1-12 orchestrator/state/run.sha 2>/dev/null) pinned -> orchestrator/state/run.sha"

done_tag() { git rev-parse -q --verify "refs/tags/$1-done" >/dev/null 2>&1; }

while : ; do
  [ -f orchestrator/STOP ] && { echo "[supervisor] STOP sentinel — exiting"; break; }
  if done_tag doc09; then echo "[supervisor] ALL DOCS DONE 🎉 — exiting"; break; fi

  # First doc lacking a -done tag AT OR AFTER the start-floor (so an operator's `--from doc02`
  # is honored across restarts and earlier already-done docs are never re-selected).
  start=""; seen_floor=0
  for d in "${DOCS[@]}"; do
    [ "$d" = "$START_FLOOR" ] && seen_floor=1
    [ "$seen_floor" -eq 1 ] || continue
    done_tag "$d" || { start="$d"; break; }
  done
  [ -z "$start" ] && { echo "[supervisor] no unfinished doc at/after $START_FLOOR (doc09 untagged?) — check state"; break; }

  # "stuck" = restarted on the SAME doc AND no new commits happened (no progress). A doc that
  # simply needs more time (commits growing every restart) is NOT stuck. A TRANSIENT network
  # preflight failure (last_rc=4) is NEVER "content stuck" — it has its own bounded counter below.
  commits_now=$(git rev-list --count HEAD 2>/dev/null || echo 0)
  if [ "$last_rc" = "4" ]; then
    :                                   # network retry — leave the content-stuck counter untouched
  elif [ "$start" = "$last" ] && [ "$commits_now" = "${commits_last:-0}" ]; then
    stuck=$((stuck+1))
  else
    stuck=0
  fi
  last="$start"; commits_last="$commits_now"
  if [ "$stuck" -ge "$STUCK_LIMIT" ]; then
    both "######################################################################"
    both "[supervisor] HALT — $start failed to advance ${STUCK_LIMIT}x in a row with no new commits."
    both "[supervisor] Every subprocess is bounded + group-killed, so this is NOT a technical hang:"
    both "[supervisor] it is a GENUINE blocker (spec ambiguity / missing credential / real coverage"
    both "[supervisor] gap) that needs a human decision. Do not just relaunch — read the evidence:"
    both "[supervisor]   • real underlying reason (preflight/phase):"
    last_reason | sed 's/^/[supervisor]       /' | tee -a "$RUNLOG"
    both "[supervisor]   • tail of orchestrator/run.log:"
    tail -25 "$RUNLOG" | sed 's/^/[supervisor]       /'
    both "[supervisor]   • also: PROGRESS.md · evidence/$start-*.md · git log --oneline -15"
    both "######################################################################"
    break
  fi

  i=$((i+1))
  if [ "$i" -gt "$MAX_RESTARTS" ]; then both "[supervisor] max restarts ($MAX_RESTARTS) — exiting"; break; fi

  echo "=================================================================="
  echo "[supervisor] launch #$i — building from $start — $(date '+%F %T')"
  echo "=================================================================="
  # Auto-apply monitoring-side fixes ONLY at a doc boundary: the conductor touches
  # orchestrator/state/ALLOW_PULL between docs and removes it while a doc builds. Fast-forward
  # ONLY, so a founder push can never rebase-mutate a live run pinned to its launch SHA.
  if [ -f orchestrator/state/ALLOW_PULL ]; then
    git pull --ff-only -q origin main 2>/dev/null \
      || echo "[supervisor] ff-only pull declined (diverged from remote) — staying on local HEAD"
  else
    echo "[supervisor] ALLOW_PULL absent — run pinned to launch SHA $(cut -c1-12 orchestrator/state/run.sha 2>/dev/null); skipping pull"
  fi
  # Conductor command is overridable ONLY for tests (default = the real conductor); production
  # path is byte-identical to before. A test can point CONDUCTOR_CMD at a stub that exits 3/4/etc.
  ${CONDUCTOR_CMD:-.venv/bin/python orchestrator/orchestrate.py} --from "$start"   # inherits its own logging
  rc=$?; last_rc=$rc
  echo "[supervisor] conductor exited rc=$rc from $start at $(date '+%F %T')"

  # Distinct preflight exit codes (orchestrate.py cli_preflight) — never silently loop on either:
  #   3 = GENUINE auth failure. A human must `claude` + /login; retrying is pointless. Halt NOW.
  #   4 = TRANSIENT network/API degradation. Pause + exponential-ish backoff; resume automatically
  #       when connectivity returns. Bounded by NET_LIMIT so it can't back off forever in the dark.
  if [ "$rc" -eq 3 ]; then
    both "######################################################################"
    both "[supervisor] HALT — AUTH: claude CLI is not authenticated in THIS terminal."
    both "[supervisor] A human must run \`claude\` here, /login (Max subscription), exit, then"
    both "[supervisor] relaunch orchestrator/launch.sh. This is NOT retryable — not a content gap."
    last_reason | sed 's/^/[supervisor]       /' | tee -a "$RUNLOG"
    both "######################################################################"
    break
  fi
  if [ "$rc" -eq 4 ]; then
    net_fail=$((net_fail+1))
    if [ "$net_fail" -ge "$NET_LIMIT" ]; then
      both "######################################################################"
      both "[supervisor] HALT — NETWORK/API DEGRADED: the claude -p preflight failed ${NET_LIMIT}x in a row."
      both "[supervisor] This is CONNECTIVITY, not a content blocker and not auth. The run is PAUSED,"
      both "[supervisor] not broken. Relaunch orchestrator/launch.sh when connectivity is back —"
      both "[supervisor] resume skips every sealed doc, so nothing is lost."
      last_reason | sed 's/^/[supervisor]       /' | tee -a "$RUNLOG"
      both "######################################################################"
      break
    fi
    backoff=$(( NET_BACKOFF * net_fail )); [ "$backoff" -gt 600 ] && backoff=600
    both "[supervisor] transient network preflight failure #${net_fail}/${NET_LIMIT} — backing off ${backoff}s (NOT a content halt)"
    sleep "$backoff"
    continue
  fi
  net_fail=0
  sleep $(( 5 + stuck * 20 ))     # gentle backoff between genuine build restarts (5s, 25s, 45s…)
done
echo "[supervisor] final state:"; git tag -l '*-done' | sort
