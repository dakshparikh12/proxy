#!/usr/bin/env bash
# orchestrator/launch.sh — THE one command a founder runs to (re)start the autonomous build from
# wherever it left off. It does everything the old two-pane ritual did, safely and in one shot:
#   • refuses to double-launch if a run is genuinely LIVE (never mutates a live run's state),
#   • clears ONLY genuinely-stale state (a dead leftover tmux session, a stale STOP sentinel),
#   • picks the resume floor = first doc lacking BOTH a -done tag AND a seal (operator can override),
#   • starts the SUPERVISOR in a DETACHED tmux session under `caffeinate -dimsu` (whole-tree sleep
#     guard; survives terminal closure),
#   • starts the WATCHDOG OUTSIDE that tmux (nohup + its own caffeinate) so it OUTLIVES — and can
#     therefore report — the supervisor's tmux dying,
#   • confirms BOTH are alive before returning control, and prints exactly how to attach/tail/stop.
#
#   bash orchestrator/launch.sh            # resume from the first unfinished doc
#   bash orchestrator/launch.sh doc05      # force a start floor
#   touch orchestrator/STOP                # stop the run cleanly
set -uo pipefail
cd "$(dirname "$0")/.."

SESSION="${SUPERVISOR_SESSION:-proxy-supervisor}"
CONSOLE="orchestrator/console.log"
WATCHLOG="orchestrator/watchdog.log"
DOCS=(doc00 doc01 doc02 doc03 doc04 doc05 doc08 doc09)

die() { echo "launch: ERROR — $*" >&2; exit 1; }

# 0. Preconditions — fail loud, never half-start.
command -v tmux >/dev/null 2>&1 || die "tmux not installed (brew install tmux)."
command -v caffeinate >/dev/null 2>&1 || die "caffeinate not found (macOS only)."
[ -x .venv/bin/python ] || die ".venv missing — run: uv venv --python 3.12 && uv sync"

# 1. Refuse to double-launch. A genuinely-live run = its tmux session up AND supervise.sh running.
#    We must NEVER touch a live run's state (that is 'mid-run', not 'stale').
if tmux has-session -t "$SESSION" 2>/dev/null && pgrep -f "orchestrator/supervise.sh" >/dev/null 2>&1; then
  die "a run is ALREADY LIVE (tmux '$SESSION' + supervise.sh). Attach: tmux attach -t $SESSION — or stop it first: touch orchestrator/STOP"
fi

# 2. Clear ONLY genuinely-stale state (we now know no live supervisor is running):
#    • a leftover tmux session whose supervisor already exited (halt/crash, kept via remain-on-exit),
#    • a STOP sentinel from a previous clean stop (would make a fresh supervisor exit instantly).
#    run.sha is re-pinned by supervise.sh at launch, so it needs no explicit clearing.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "launch: reaping stale tmux session '$SESSION' (its supervisor is not running)"
  tmux kill-session -t "$SESSION" 2>/dev/null || true
fi
if [ -f orchestrator/STOP ]; then
  echo "launch: removing stale STOP sentinel (a fresh launch means 'go')"
  rm -f orchestrator/STOP
fi

# 3. Resume floor = first doc lacking BOTH a -done tag AND a seal.json (= where we left off).
#    Operator override via arg 1. supervise.sh (untagged-doc scan) and run_doc (_doc_already_complete)
#    are two further safety nets, so a sealed-but-passing doc reached here is still skipped.
floor="${1:-}"
if [ -z "$floor" ]; then
  floor="doc09"
  for d in "${DOCS[@]}"; do
    if ! git rev-parse -q --verify "refs/tags/$d-done" >/dev/null 2>&1 \
       && [ ! -f "orchestrator/state/$d.seal.json" ]; then
      floor="$d"; break
    fi
  done
elif ! printf '%s\n' "${DOCS[@]}" | grep -qx "$floor"; then
  die "'$floor' is not a known doc (${DOCS[*]})."
fi
echo "launch: resume floor = $floor"

# Optional test seam (mirrors supervise.sh): a stub conductor, propagated into the tmux env.
SUP_ENV=""
[ -n "${CONDUCTOR_CMD:-}" ] && SUP_ENV="CONDUCTOR_CMD=$CONDUCTOR_CMD "

# 4. Supervisor: DETACHED tmux session, whole-tree sleep guard. remain-on-exit keeps the pane
#    readable after a halt (attach and read the banner); a GONE session then unambiguously means
#    the tmux server itself died — exactly what the watchdog's severe check reports.
tmux new-session -d -s "$SESSION" \
  "exec env ${SUP_ENV}caffeinate -dimsu bash orchestrator/supervise.sh $floor 2>&1 | tee -a $CONSOLE"
tmux set-option -t "$SESSION" remain-on-exit on 2>/dev/null || true

# 5. Watchdog: OUTSIDE tmux (nohup + own caffeinate) so it survives the supervisor's tmux dying and
#    can report it. WATCH_TMUX=1 arms the dead-session check against our session name.
WATCH_TMUX=1 SUPERVISOR_SESSION="$SESSION" nohup caffeinate -dimsu \
  bash orchestrator/watchdog.sh >> "$WATCHLOG" 2>&1 &
WD_PID=$!
disown "$WD_PID" 2>/dev/null || true

# 6. Confirm BOTH are alive before returning control.
sleep 3
ok=1
if tmux has-session -t "$SESSION" 2>/dev/null && pgrep -f "orchestrator/supervise.sh" >/dev/null 2>&1; then
  echo "launch: ✅ supervisor ALIVE — tmux '$SESSION', supervise.sh pid $(pgrep -f 'orchestrator/supervise.sh' | head -1)"
else
  echo "launch: ❌ supervisor did NOT come up — inspect: tmux attach -t $SESSION ; tail $CONSOLE"; ok=0
fi
if pgrep -f "orchestrator/watchdog.sh" >/dev/null 2>&1; then
  echo "launch: ✅ watchdog ALIVE — external (nohup), pings on any stall / halt / tmux-death (pid $(pgrep -f 'orchestrator/watchdog.sh' | head -1))"
else
  echo "launch: ❌ watchdog did NOT come up — inspect: tail $WATCHLOG"; ok=0
fi

echo "launch: attach live   -> tmux attach -t $SESSION      (detach with Ctrl-b then d)"
echo "launch: tail progress -> tail -f orchestrator/run.log"
echo "launch: stop cleanly  -> touch orchestrator/STOP"
if [ "$ok" -eq 1 ]; then
  echo "launch: 🚀 BOTH ALIVE from $floor — you may close this terminal; the run continues (keep the charger in)."
else
  die "one or both did not start — see above."
fi
