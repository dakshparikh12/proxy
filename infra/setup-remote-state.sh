#!/usr/bin/env bash
# === setup-remote-state.sh ===
# Bootstraps the remote Terraform state backend for an env before any stack is
# applied. Creates the versioned GCS state bucket the bootstrap stack expects.
#
# apply order per env: bootstrap -> platform. Run this once per env, then apply
# the bootstrap stack, then the platform stack.
#
# Usage: ./setup-remote-state.sh <project_id> <env>
set -euo pipefail

PROJECT_ID="${1:?usage: setup-remote-state.sh <project_id> <env>}"
ENV="${2:?usage: setup-remote-state.sh <project_id> <env>}"
REGION="${REGION:-us-central1}"

STATE_BUCKET="tfstate-${PROJECT_ID}-${ENV}"

echo "Creating remote-state bucket gs://${STATE_BUCKET} in ${REGION} ..."
gcloud storage buckets create "gs://${STATE_BUCKET}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --uniform-bucket-level-access

# Object Versioning protects the state history from accidental overwrite.
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning

echo "Remote state ready. Next: terraform -chdir=envs/${ENV}/bootstrap apply,"
echo "then terraform -chdir=envs/${ENV}/platform apply (bootstrap before platform)."
