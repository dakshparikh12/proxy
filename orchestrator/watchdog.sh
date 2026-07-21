#!/usr/bin/env bash
# orchestrator/watchdog.sh — the EXTERNAL observer. Launched by orchestrator/launch.sh OUTSIDE the
# supervisor's tmux (nohup, its own caffeinate) so it SURVIVES the supervisor's tmux server dying —
# the exact failure it must report. Polls every 4 min and sends an ntfy.sh push on, in severity order:
#   (0) the supervisor's tmux SESSION/SERVER is GONE — the whole run died (sleep/crash/closure): the
#       MOST severe signal, louder than any single process exiting. It must NEVER be met with silence.
#   (1) supervise.sh not running though its tmux session is still alive — a halt/crash within a live
#       session (attach and read the banner).
#   (2) a loud [supervisor] HALT banner (a doc failed to advance / auth / network).
#   (3) a genuine STALL — console.log (the live agent stream) untouched for STALL_MIN minutes.
#
# WHY console-mtime, NOT "no new git commits": the conductor commits only at PHASE BOUNDARIES. P1/P3/P5
#   legitimately run 20-34 min committing NOTHING (doc03 P1 caps at 27m; doc04/08 at 34m). A commit-based
#   stall detector would cry wolf every ~12 min through every normal generation phase — training you to
#   ignore it. run_agent's drain thread live-echoes every agent line to stdout, which supervise.sh tee's
#   into console.log — so a WORKING phase touches console.log every few SECONDS at ANY phase, and a
#   genuine freeze (the 14-40 min near-idle stall this project exists to catch) leaves it untouched.
#   STALL_MIN=20 (5 polls) sits safely ABOVE the longest legit inter-line gap yet well below the phase
#   caps, so it never false-fires on a quiet-but-working phase and never kills (it only notifies).
set -uo pipefail
cd "$(dirname "$0")/.."

INTERVAL="${INTERVAL:-240}"                 # poll every 4 min
STALL_MIN="${STALL_MIN:-20}"                # console.log untouched this long = genuine stall (5 polls)
NTFY="${NTFY:-ntfy.sh/proxy-overnight-8f3k}"
SUPERVISOR_SESSION="${SUPERVISOR_SESSION:-proxy-supervisor}"   # tmux session launch.sh runs the supervisor in
WATCH_TMUX="${WATCH_TMUX:-0}"               # 1 => this watchdog owns the tmux-liveness check (set by launch.sh)
CONSOLE="orchestrator/console.log"
RUNLOG="orchestrator/run.log"
stalled=0                                   # de-dupe: alert once per stall episode, re-arm when it recovers

ping() { curl -s -d "$1" "$NTFY" >/dev/null 2>&1 || true; echo "[watchdog] $(date '+%F %T') PING: $1"; }

# Portable mtime-age in seconds (BSD stat on macOS, GNU stat on Linux).
age_secs() {
  local f="$1" now m
  now=$(date +%s)
  m=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo "$now")
  echo $(( now - m ))
}

# Is the supervisor's tmux session still present? Only meaningful when launch.sh set WATCH_TMUX=1
# (a manual `bash watchdog.sh` with no tmux must NOT false-fire the dead-tmux alarm).
supervisor_tmux_alive() {
  [ "$WATCH_TMUX" != "1" ] && return 0                       # not tmux-managed => treat as "alive" (skip)
  command -v tmux >/dev/null 2>&1 || return 0
  tmux has-session -t "$SUPERVISOR_SESSION" 2>/dev/null
}

echo "[watchdog] started $(date '+%F %T') — poll ${INTERVAL}s, stall=${STALL_MIN}m of console silence, tmux-guard=${WATCH_TMUX}(${SUPERVISOR_SESSION}), ntfy=$NTFY"

while true; do
  sleep "$INTERVAL"

  # (0) MOST SEVERE: the supervisor's tmux session/server is GONE. If the console shows the clean
  #     done banner it was an intentional teardown; otherwise the whole run died (sleep/crash/closure)
  #     and would otherwise vanish with NO notification. This is the case the old watchdog missed.
  if ! supervisor_tmux_alive; then
    if tail -5 "$CONSOLE" 2>/dev/null | grep -q "ALL DOCS DONE"; then
      ping "Proxy build: DONE — supervisor tmux session '$SUPERVISOR_SESSION' closed after the clean done banner. 🎉"
    else
      ping "Proxy build: ‼️ tmux session '$SUPERVISOR_SESSION' is GONE (server died — machine sleep/crash/terminal-kill). The ENTIRE run stopped, not just a phase. Relaunch: bash orchestrator/launch.sh. Last run.log: $(tail -1 "$RUNLOG" 2>/dev/null | cut -c1-90)"
    fi
    break
  fi

  # (1) Supervisor process gone but its session is still alive — it persists across conductor
  #     restarts, so its absence = the build halted or crashed (attach to the session and read).
  if ! pgrep -f "orchestrator/supervise.sh" >/dev/null 2>&1; then
    if tail -5 "$CONSOLE" 2>/dev/null | grep -q "ALL DOCS DONE"; then
      ping "Proxy build: ALL DOCS DONE — supervisor exited cleanly. 🎉"
    else
      ping "Proxy build: supervise.sh is NOT running — halted or crashed (not the clean done banner). tmux '$SUPERVISOR_SESSION' still up; attach and read the banner."
    fi
    break
  fi

  # (2) Loud halt? supervise.sh prints this banner ONLY when a doc failed to advance / auth / network.
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
    echo "[watchdog] $(date '+%F %T') alive — session up, console fresh; last: $(tail -1 "$RUNLOG" 2>/dev/null | cut -c1-90)"
  fi
done
echo "[watchdog] exiting $(date '+%F %T')"
