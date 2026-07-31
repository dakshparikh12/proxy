#!/usr/bin/env bash
# build-and-push.sh — build the control-plane image and push it to Artifact Registry.
#
# The ONE deployable (SPEC §9). Run from the repo root AFTER `terraform apply` has
# created the Artifact Registry repo. Prints the exact image ref to pass back to
# Terraform as -var="image=<ref>".
#
# Usage:
#   PROJECT=proxy-meeting-dev REGION=us-central1 TAG=v0 bash deploy/build-and-push.sh
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<gcp-project-id> (e.g. proxy-meeting-dev)}"
REGION="${REGION:-us-central1}"
TAG="${TAG:-v0}"
SERVICE="${SERVICE:-proxy-control-plane}"

REPO="${REGION}-docker.pkg.dev/${PROJECT}/proxy"
IMAGE="${REPO}/${SERVICE}:${TAG}"

# Authenticate docker to Artifact Registry (idempotent).
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# Build from the repo root with the control-plane Dockerfile.
docker build -f deploy/control-plane/Dockerfile -t "${IMAGE}" .
docker push "${IMAGE}"

echo
echo "pushed: ${IMAGE}"
echo "next:   terraform -chdir=terraform apply -var=\"project_id=${PROJECT}\" -var=\"image=${IMAGE}\""
