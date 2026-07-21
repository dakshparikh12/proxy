#!/usr/bin/env bash
# orchestrator/watchdog.sh — runs alongside supervise.sh (own tmux pane), polls every 4 min and
# sends an ntfy.sh push on a genuine STALL, a loud HALT, or the supervisor stopping.
#
# WHY these three signals — and specifically NOT "no new git commits":
#   The conductor commits only at PHASE BOUNDARIES. P1/P3/P5 legitimately run 15-35 min committing
#   NOTHING (P1 for doc03 is capped at 27 min; P5 planning 15-20+ min; P3 evidence ~25 min). A
#   commit-based stall detector therefore cries wolf every ~12 min through every normal generation
#   phase — training you to ignore it. The real liveness signal is orchestrator/console.log GROWING:
#   run_agent's drain thread live-echoes every agent line to the conductor's stdout, which
#   supervise.sh tee's into console.log — so a WORKING phase touches console.log every few seconds,
#   and a genuine freeze (the 14-40 min near-idle stall this whole project exists to catch) leaves it
#   untouched. That is the signal, at any phase, with no false positives on quiet-but-working phases.
#
# Launch it in its own pane AFTER supervise.sh is up (see the launch block printed by the audit).
set -uo pipefail
cd "$(dirname "$0")/.."

INTERVAL="${INTERVAL:-240}"          # poll every 4 min
STALL_MIN="${STALL_MIN:-15}"         # console.log untouched this long = genuine stall (freeze was 14-40m)
NTFY="${NTFY:-ntfy.sh/proxy-overnight-8f3k}"
CONSOLE="orchestrator/console.log"
RUNLOG="orchestrator/run.log"
stalled=0                            # de-dupe: alert once per stall episode, re-arm when it recovers

ping() { curl -s -d "$1" "$NTFY" >/dev/null 2>&1 || true; echo "[watchdog] $(date '+%F %T') PING: $1"; }

# Portable mtime-age in seconds (BSD stat on macOS, GNU stat on Linux).
age_secs() {
  local f="$1" now m
  now=$(date +%s)
  m=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo "$now")
  echo $(( now - m ))
}

echo "[watchdog] started $(date '+%F %T') — poll ${INTERVAL}s, stall=${STALL_MIN}m of console silence, ntfy=$NTFY"

while true; do
  sleep "$INTERVAL"

  # (1) Supervisor gone? It persists across conductor restarts, so its absence = build over/crashed.
  if ! pgrep -f "orchestrator/supervise.sh" >/dev/null 2>&1; then
    if tail -5 "$CONSOLE" 2>/dev/null | grep -q "ALL DOCS DONE"; then
      ping "Proxy build: ALL DOCS DONE — supervisor exited cleanly. 🎉"
    else
      ping "Proxy build: supervise.sh is NOT running — halted or crashed (not the clean done banner). Check the pane."
    fi
    break
  fi

  # (2) Loud halt? supervise.sh prints this banner ONLY when a doc failed to advance N× (needs a human).
  if tail -80 "$CONSOLE" 2>/dev/null | grep -q "\[supervisor\] HALT"; then
    ping "Proxy build HALTED — needs a human: $(grep '\[supervisor\] HALT' "$CONSOLE" 2>/dev/null | tail -1)"
    break
  fi

  # (3) Genuine stall? console.log (the live agent stream) untouched for STALL_MIN minutes.
  if [ -f "$CONSOLE" ] && [ "$(age_secs "$CONSOLE")" -ge $(( STALL_MIN * 60 )) ]; then
    if [ "$stalled" -eq 0 ]; then
      ping "Proxy build STALL: console.log silent ${STALL_MIN}m at near-idle (possible freeze). Last run.log: $(tail -1 "$RUNLOG" 2>/dev/null)"
      stalled=1
    fi
  else
    [ "$stalled" -eq 1 ] && echo "[watchdog] $(date '+%F %T') recovered — console.log fresh again"
    stalled=0
    echo "[watchdog] $(date '+%F %T') alive — console fresh; last: $(tail -1 "$RUNLOG" 2>/dev/null | cut -c1-90)"
  fi
done
echo "[watchdog] exiting $(date '+%F %T')"
