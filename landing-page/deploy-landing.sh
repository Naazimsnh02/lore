#!/usr/bin/env bash
# deploy-landing.sh — Build and deploy LORE's landing page to Cloud Run.

set -euo pipefail

PROJECT_ID=""
REGION="us-central1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project <PROJECT_ID> is required"
  exit 1
fi

NAME="lore-landing-page"
IMAGE="gcr.io/${PROJECT_ID}/${NAME}:latest"

echo "==> Deploying LORE Landing Page"
echo "    Project: ${PROJECT_ID}"
echo "    Region : ${REGION}"
echo ""

# Build
gcloud builds submit landing-page/ \
  --tag "${IMAGE}" \
  --project "${PROJECT_ID}"

# Deploy
gcloud run deploy "${NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --project "${PROJECT_ID}"

URL=$(gcloud run services describe "${NAME}" --region "${REGION}" --format 'value(status.url)' --project "${PROJECT_ID}")
echo ""
echo "==> Landing page deployed: ${URL}"
