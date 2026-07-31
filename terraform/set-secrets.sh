#!/usr/bin/env bash
# set-secrets.sh — populate the OUT-OF-BAND secret values into Secret Manager.
#
# The Terraform module (terraform/) creates the secret RESOURCES but never their
# vendor/auth VALUES (CLAUDE.md hard rule: secrets only from Secret Manager, never
# a Terraform literal). This script adds a version to each externally-populated
# secret, reading the value from your environment so nothing is ever typed on the
# command line (no shell-history leak) or written to disk.
#
# Usage:
#   1. export each var below (e.g. from a local, gitignored .env you `source`):
#        export RECALL_API_KEY=... CARTESIA_API_KEY=... E2B_API_KEY=... etc.
#   2. run:  PROJECT=proxy-meeting-dev bash terraform/set-secrets.sh
#
# Idempotent: each run adds a NEW secret VERSION; the service reads :latest.
# The Terraform-generated secrets (AES keys, SESSION_*, tokens, DATABASE_URL) are
# NOT set here — Terraform already populated them.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<gcp-project-id> (e.g. proxy-meeting-dev)}"

# The externally-populated secret ids — MUST match terraform/secrets.tf's
# local.external_secrets. Each expects an env var of the SAME name.
EXTERNAL_SECRETS=(
  RECALL_API_KEY
  RECALL_WEBHOOK_SECRET
  CARTESIA_API_KEY
  ASSEMBLYAI_API_KEY
  E2B_API_KEY
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  CLAUDE_CODE_OAUTH_TOKEN   # dev: the founder's subscription token (workroom sandbox auth)
  ANTHROPIC_API_KEY         # prod: the cloud API key (control-plane Claude auth)
)

missing=()
for name in "${EXTERNAL_SECRETS[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
    continue
  fi
  printf '%s' "${!name}" | gcloud secrets versions add "$name" \
    --project="$PROJECT" --data-file=- >/dev/null
  echo "set $name"
done

if (( ${#missing[@]} > 0 )); then
  echo
  echo "NOT set (env var unset — skipped): ${missing[*]}"
  echo "Note: for DEV set CLAUDE_CODE_OAUTH_TOKEN (subscription) and you may skip"
  echo "ANTHROPIC_API_KEY; for PROD set ANTHROPIC_API_KEY (Anthropic ToS forbids"
  echo "routing customers on a personal subscription — SPEC §9). At least ONE Claude"
  echo "auth mode must be set or the control-plane boot gate fails."
fi
