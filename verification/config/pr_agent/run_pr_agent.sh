#!/usr/bin/env bash
# Layer 3 — self-hosted PR-Agent (The-PR-Agent/pr-agent) review, judged by Claude.
#
# Reuses the existing ANTHROPIC_API_KEY (sourced from the repo/parent .env) — no new
# vendor, no new account. Reviews a GitHub PR URL passed as $1 (the verification
# branch's PR is the natural target once opened).
#
# Requires: a running Docker daemon + a funded Anthropic key. In the session that
# built this, BOTH were unavailable (daemon down; Anthropic credit exhausted), so the
# live run is recorded as BLOCKED in the reports — the config is complete and
# reproducible; nothing here is faked.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PR_URL="${1:?usage: run_pr_agent.sh <github_pr_url> [review|describe|improve]}"
CMD="${2:-review}"
IMAGE="${PR_AGENT_IMAGE:-pragent/pr-agent:latest}"   # Docker Hub image per the fork's docs

# Source ANTHROPIC_API_KEY without echoing it.
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  for env in "$HERE/../../../.env" "$HERE/../../../../proxy/.env"; do
    [ -f "$env" ] && ANTHROPIC_API_KEY="$(grep -m1 '^ANTHROPIC_API_KEY=' "$env" | cut -d= -f2- | tr -d '"'"'"'"'"' \r')" && break
  done
fi
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY not set and not found in .env}"
: "${GITHUB_TOKEN:?set GITHUB_TOKEN to a read-only token for the PR's repo}"

if ! docker info >/dev/null 2>&1; then
  echo "BLOCKED: Docker daemon is not running — start Docker and re-run." >&2
  exit 3
fi

exec docker run --rm \
  -v "$HERE/configuration.toml:/app/pr_agent/settings/configuration.toml:ro" \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e CONFIG__MODEL="anthropic/claude-sonnet-4-6" \
  -e CONFIG__GIT_PROVIDER="github" \
  -e GITHUB__USER_TOKEN="$GITHUB_TOKEN" \
  -e CONFIG__PUBLISH_OUTPUT="false" \
  "$IMAGE" --pr_url "$PR_URL" "$CMD"
