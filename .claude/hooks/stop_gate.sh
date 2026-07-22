#!/usr/bin/env bash
# stop_gate.sh — StopHook: block Claude from stopping if current task is not done.
#
# Reads the current task from slices/*/.current (format: <id>:<task_id>).
# If none exists → allow stop (exit 0).
# If done-check green → allow (exit 0).
# If not green, track stall count; on 3rd identical failure, flag-and-continue.
# Otherwise emit block decision.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# ── Find the .current file ──────────────────────────────────────────────────
CURRENT_FILE=""
for f in slices/*/.current; do
    if [[ -f "$f" ]]; then
        CURRENT_FILE="$f"
        break
    fi
done

if [[ -z "$CURRENT_FILE" ]]; then
    # No active task — allow stop
    exit 0
fi

# ── Parse <id>:<task_id> ────────────────────────────────────────────────────
CURRENT_CONTENT=$(cat "$CURRENT_FILE")
ID="${CURRENT_CONTENT%%:*}"
TASK_ID="${CURRENT_CONTENT##*:}"

if [[ -z "$ID" || -z "$TASK_ID" || "$ID" == "$TASK_ID" ]]; then
    # Malformed .current — fail open
    exit 0
fi

# ── Run done-check ──────────────────────────────────────────────────────────
CHECK_OUT=$(./done-check.sh --task "$ID" "$TASK_ID" 2>&1) || true
CHECK_RC=$?

if [[ $CHECK_RC -eq 0 ]]; then
    # Task is green — allow stop
    exit 0
fi

# ── Track identical-failure stalls ──────────────────────────────────────────
STALL_FILE="slices/${ID}/.stall.${TASK_ID}"
PROGRESS_FILE="slices/${ID}/progress.md"

# Hash last ~1500 bytes of the check output
FAIL_HASH=$(printf '%s' "${CHECK_OUT: -1500}" | md5 2>/dev/null || printf '%s' "${CHECK_OUT: -1500}" | md5sum | cut -d' ' -f1)

STALL_COUNT=0
STALL_LAST_HASH=""
if [[ -f "$STALL_FILE" ]]; then
    STALL_COUNT=$(head -1 "$STALL_FILE" 2>/dev/null || echo 0)
    STALL_LAST_HASH=$(tail -1 "$STALL_FILE" 2>/dev/null || echo "")
fi

if [[ "$FAIL_HASH" == "$STALL_LAST_HASH" ]]; then
    STALL_COUNT=$((STALL_COUNT + 1))
else
    # Different failure — reset count
    STALL_COUNT=1
fi

printf '%s\n%s\n' "$STALL_COUNT" "$FAIL_HASH" > "$STALL_FILE"

if [[ $STALL_COUNT -ge 3 ]]; then
    # Flag-and-continue: never deadlock
    mkdir -p "slices/${ID}"
    echo "BLOCKED:${ID}:${TASK_ID} stalled 3x" >> "$PROGRESS_FILE"
    exit 0
fi

# ── Emit block decision ─────────────────────────────────────────────────────
TAIL=$(printf '%s' "$CHECK_OUT" | tail -5)
printf '{"decision":"block","reason":"task %s not green: %s"}\n' "$TASK_ID" "$TAIL"
exit 0
