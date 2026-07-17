#!/usr/bin/env bash
# SUPERVISOR — keep the autonomous build going until the whole product is done.
# Restarts the conductor from the first unfinished doc whenever it exits (plumbing halt, crash,
# transient), because orchestrate.py resumes instantly past any sealed/built work. Bounded so it
# can't spin forever on a genuinely-stuck problem: stops if the SAME doc fails to advance several
# times in a row (needs a human), on a STOP sentinel, or when doc09 is done.
#
# Launch (leave it; it survives conductor restarts):
#   caffeinate -i bash orchestrator/supervise.sh 2>&1 | tee -a orchestrator/console.log
# Stop it cleanly:  touch orchestrator/STOP
set -uo pipefail
cd "$(dirname "$0")/.."
DOCS=(doc00 doc01 doc02 doc03 doc04 doc05 doc08 doc09)
MAX_RESTARTS="${MAX_RESTARTS:-60}"
STUCK_LIMIT="${STUCK_LIMIT:-4}"     # same doc, no progress, N times in a row => human needed
i=0; last=""; stuck=0

# Pin this run to the SHA we launched at: a founder push can't silently mutate a live run.
# The supervisor only fast-forwards new work at a doc boundary (ALLOW_PULL present), never mid-doc.
mkdir -p orchestrator/state
git rev-parse HEAD > orchestrator/state/run.sha 2>/dev/null || true
echo "[supervisor] launch SHA $(cut -c1-12 orchestrator/state/run.sha 2>/dev/null) pinned -> orchestrator/state/run.sha"

done_tag() { git rev-parse -q --verify "refs/tags/$1-done" >/dev/null 2>&1; }

while : ; do
  [ -f orchestrator/STOP ] && { echo "[supervisor] STOP sentinel — exiting"; break; }
  if done_tag doc09; then echo "[supervisor] ALL DOCS DONE 🎉 — exiting"; break; fi

  start=""
  for d in "${DOCS[@]}"; do done_tag "$d" || { start="$d"; break; }; done
  [ -z "$start" ] && { echo "[supervisor] no unfinished doc but doc09 untagged — check state"; break; }

  # "stuck" = restarted on the SAME doc AND no new commits happened (no progress). A doc that
  # simply needs more time (commits growing every restart) is NOT stuck.
  commits_now=$(git rev-list --count HEAD 2>/dev/null || echo 0)
  if [ "$start" = "$last" ] && [ "$commits_now" = "${commits_last:-0}" ]; then
    stuck=$((stuck+1))
  else
    stuck=0
  fi
  last="$start"; commits_last="$commits_now"
  if [ "$stuck" -ge "$STUCK_LIMIT" ]; then
    echo "[supervisor] $start failed to advance ${STUCK_LIMIT}x in a row — a real blocker needs a human."
    echo "[supervisor] Read: orchestrator/run.log · PROGRESS.md · evidence/$start-*.md · git log. Exiting."
    break
  fi

  i=$((i+1))
  if [ "$i" -gt "$MAX_RESTARTS" ]; then echo "[supervisor] max restarts ($MAX_RESTARTS) — exiting"; break; fi

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
  .venv/bin/python orchestrator/orchestrate.py --from "$start"   # inherits its own logging
  rc=$?
  echo "[supervisor] conductor exited rc=$rc from $start at $(date '+%F %T')"
  sleep 5
done
echo "[supervisor] final state:"; git tag -l '*-done' | sort
