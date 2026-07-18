#!/usr/bin/env bash
# promote.sh — ship an already-verified dev image to prod.
#
# Prod is NEVER built or pushed to directly. This promote step re-tags the exact
# image digest that Cloud Build (dev, on trigger) already built, migrated, and
# deployed, then rolls it out to the prod Cloud Run service. Migrations for prod
# run here as their own separate step (alembic upgrade head) before the deploy,
# mirroring the migrate-db step in deploy/cloudbuild.yaml.
set -euo pipefail

REGION="${REGION:-us-central1}"
AR_REPO="${AR_REPO:-us-central1-docker.pkg.dev/${PROJECT_ID}/proxy}"
SERVICE="${SERVICE:-proxy-control-plane}"
SHA="${1:?usage: promote.sh <git-sha-already-on-dev>}"

IMAGE="${AR_REPO}/${SERVICE}:${SHA}"

echo "promote: verifying image exists in Artifact Registry: ${IMAGE}"
gcloud artifacts docker images describe "${IMAGE}" >/dev/null

# migrate prod as a SEPARATE step, before the deploy
echo "promote: running prod migrations (alembic upgrade head)"
docker run --rm "${IMAGE}" python -m alembic upgrade head

echo "promote: deploying to prod Cloud Run"
gcloud run deploy "${SERVICE}-prod" \
  --image="${IMAGE}" \
  --region="${REGION}"

echo "promote: done"
