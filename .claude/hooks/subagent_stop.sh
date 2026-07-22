#!/usr/bin/env bash
# subagent_stop.sh — SubagentStop hook: block subagent fold-back if it made
# out-of-scope writes or introduced secret literals.
#
# Checks:
#   1. Any modified/added file under builder-read-only paths.
#   2. Secret literal patterns in the diff (sk- tokens, api-key/bearer headers).
#
# Never throws. Always exits 0 (block decision sent via JSON print).
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# ── Builder-read-only path check ────────────────────────────────────────────
# Get changed paths (staged + unstaged)
CHANGED_PATHS=$(git status --porcelain 2>/dev/null | awk '{print $2}' || true)

PROTECTED_PATHS=""
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    # Check if path is under any builder-read-only directory
    if echo "$path" | grep -qE '^(tests|acceptance|fixtures|goldens|criteria|product)/' ; then
        PROTECTED_PATHS="${PROTECTED_PATHS} ${path}"
    elif echo "$path" | grep -qE '_baseline\.json$'; then
        PROTECTED_PATHS="${PROTECTED_PATHS} ${path}"
    fi
done <<< "$CHANGED_PATHS"

if [[ -n "$PROTECTED_PATHS" ]]; then
    TRIMMED="${PROTECTED_PATHS# }"
    printf '{"decision":"block","reason":"subagent wrote to builder-read-only: %s"}\n' "$TRIMMED"
    exit 0
fi

# ── Secret literal scan ─────────────────────────────────────────────────────
# Get the full diff of all changes
DIFF=$(git diff 2>/dev/null; git diff --cached 2>/dev/null) || true

# Check for sk- prefixed tokens (20+ chars), but NOT 32-hex trace IDs
# Pattern: sk- followed by 20+ alphanumeric/special chars that are NOT pure hex
if echo "$DIFF" | grep -qP '^[+].*\bsk-[A-Za-z0-9_\-]{20,}' 2>/dev/null; then
    # Exclude lines that look like pure 32-char hex (trace IDs)
    if echo "$DIFF" | grep -P '^[+].*\bsk-[A-Za-z0-9_\-]{20,}' 2>/dev/null | grep -qvP '\bsk-[0-9a-f]{32}\b'; then
        printf '{"decision":"block","reason":"subagent introduced secret literal (sk- token)"}\n'
        exit 0
    fi
fi

# Check for x-api-key or authorization: bearer with non-REDACTED 12+ char values
if echo "$DIFF" | grep -qiP '^[+].*(?:x-api-key|authorization:\s*bearer)\s*[:\s]+(?!REDACTED)[A-Za-z0-9_\-\.]{12,}' 2>/dev/null; then
    printf '{"decision":"block","reason":"subagent introduced secret literal (api-key/bearer)"}\n'
    exit 0
fi

# ── All clear ───────────────────────────────────────────────────────────────
exit 0
