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
# Portable regex via python3. Do NOT use `grep -P` here: BSD grep (macOS
# /usr/bin/grep, which /bin/bash resolves) has no PCRE and errors on -P (rc 2),
# which silently disabled this whole scan. python3 is always present.
DIFF=$(git diff 2>/dev/null; git diff --cached 2>/dev/null) || true

SECRET_REASON=$(DIFF_TEXT="$DIFF" python3 - <<'PY'
import os, re, sys

# Pass the diff via env, NOT stdin: `python3 -` reads its script from the
# heredoc (stdin), so piping $DIFF in would be swallowed and the scan would
# match nothing. Only inspect added lines ('+', excluding the '+++' header).
added = "\n".join(
    ln for ln in os.environ.get("DIFF_TEXT", "").splitlines()
    if ln.startswith("+") and not ln.startswith("+++")
)

# sk- tokens (20+ chars) — but NOT a pure 32-hex trace ID (sk-<32 hex>).
for m in re.finditer(r"\bsk-[A-Za-z0-9_\-]{20,}", added):
    if not re.fullmatch(r"sk-[0-9a-f]{32}", m.group(0)):
        print("sk- token")
        sys.exit(0)

# x-api-key / authorization: bearer with a non-REDACTED 12+ char value.
if re.search(
    r"(?i)(x-api-key|authorization:\s*bearer)\s*[:\s]+(?!REDACTED)[A-Za-z0-9_\-.]{12,}",
    added,
):
    print("api-key/bearer")
    sys.exit(0)
PY
)

if [[ -n "$SECRET_REASON" ]]; then
    printf '{"decision":"block","reason":"subagent introduced secret literal (%s)"}\n' "$SECRET_REASON"
    exit 0
fi

# ── All clear ───────────────────────────────────────────────────────────────
exit 0
