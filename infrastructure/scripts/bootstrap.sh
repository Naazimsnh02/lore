#!/usr/bin/env bash
# bootstrap.sh — One-time GCP project setup for LORE.
#
# What it does:
#   1. Sets the active gcloud project
#   2. Enables all required GCP APIs
#   3. Creates the lore-backend service account with required roles
#   4. Prints next steps for deploying the 3 active Cloud Run services
#
# Active services:
#   gemini_live_proxy  :8090  — WebSocket proxy to Gemini Live API
#   nano_illustrator   :8091  — HTTP image generation
#   veo_generator      :8092  — HTTP video generation
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - Owner / Editor role on the GCP project
#   - Project already exists with billing enabled
#
# Usage:
#   ./infrastructure/scripts/bootstrap.sh <PROJECT_ID> [REGION]
#
# Example:
#   ./infrastructure/scripts/bootstrap.sh my-lore-project us-central1

set -euo pipefail

PROJECT_ID="${1:?Usage: bootstrap.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SA_NAME="lore-backend"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Bootstrapping LORE for project: ${PROJECT_ID} (region: ${REGION})"

# ── 1. Set active project ────────────────────────────────────────────────────
echo "--> Setting gcloud project"
gcloud config set project "${PROJECT_ID}"

# ── 2. Enable required APIs ──────────────────────────────────────────────────
echo "--> Enabling required GCP APIs"
gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  places.googleapis.com \
  maps-backend.googleapis.com \
  maps-android-backend.googleapis.com \
  maps-ios-backend.googleapis.com \
  directions-backend.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="${PROJECT_ID}"

echo "    APIs enabled."

# ── 3. Create service account ────────────────────────────────────────────────
echo "--> Creating service account: ${SA_EMAIL}"
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "    Service account already exists — skipping creation."
else
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="LORE Backend" \
    --project="${PROJECT_ID}"
fi

# ── 4. Grant required IAM roles ──────────────────────────────────────────────
echo "--> Granting IAM roles to ${SA_EMAIL}"
for ROLE in \
  "roles/aiplatform.user" \
  "roles/secretmanager.secretAccessor" \
  "roles/logging.logWriter" \
  "roles/monitoring.metricWriter"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet
done
echo "    Roles granted."

# ── 5. Store Places API key in Secret Manager ────────────────────────────────
echo ""
echo "==> Bootstrap complete!"
echo ""
echo "Next steps:"
echo ""
echo "  1. Store your Places API key in Secret Manager:"
echo "       echo -n 'YOUR_PLACES_API_KEY' | \\"
echo "         gcloud secrets create lore-places-api-key --data-file=- --project=${PROJECT_ID}"
echo ""
echo "  2. Grant the service account access to the secret:"
echo "       gcloud secrets add-iam-policy-binding lore-places-api-key \\"
echo "         --member='serviceAccount:${SA_EMAIL}' \\"
echo "         --role='roles/secretmanager.secretAccessor' \\"
echo "         --project=${PROJECT_ID}"
echo ""
echo "  3. Deploy the 3 Cloud Run services:"
echo "       ./infrastructure/scripts/deploy.sh --project ${PROJECT_ID} --region ${REGION}"
echo ""
