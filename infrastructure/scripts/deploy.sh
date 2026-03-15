#!/usr/bin/env bash
# deploy.sh — Build and deploy LORE's 3 active Cloud Run services.
#
# Services deployed:
#   lore-gemini-proxy      — WebSocket proxy to Gemini Live API (port 8090)
#   lore-nano-illustrator  — HTTP image generation (port 8091)
#   lore-veo-generator     — HTTP video generation (port 8092)
#
# Usage:
#   ./infrastructure/scripts/deploy.sh [OPTIONS]
#
# Options:
#   --project  <PROJECT_ID>   GCP project ID (required)
#   --region   <REGION>       Cloud Run region (default: us-central1)
#   --service  <NAME>         Deploy only one service: proxy | images | video
#   --vertex                  Use Vertex AI (default: false = AI Studio)
#
# Prerequisites:
#   - bootstrap.sh has been run
#   - gcloud CLI authenticated: gcloud auth login
#   - GEMINI_API_KEY set in environment (for AI Studio mode)
#     OR Vertex AI enabled with ADC configured (for --vertex mode)
#
# Example — deploy all services with Vertex AI:
#   ./infrastructure/scripts/deploy.sh --project my-lore-project --vertex
#
# Example — redeploy only the proxy:
#   ./infrastructure/scripts/deploy.sh --project my-lore-project --service proxy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROJECT_ID=""
REGION="us-central1"
SERVICE="all"
USE_VERTEX="false"

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)  PROJECT_ID="$2"; shift 2 ;;
    --region)   REGION="$2"; shift 2 ;;
    --service)  SERVICE="$2"; shift 2 ;;
    --vertex)   USE_VERTEX="true"; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project <PROJECT_ID> is required"
  exit 1
fi

SA_EMAIL="lore-backend@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_BASE="gcr.io/${PROJECT_ID}"

echo "==> LORE Cloud Run Deploy"
echo "    Project    : ${PROJECT_ID}"
echo "    Region     : ${REGION}"
echo "    Service    : ${SERVICE}"
echo "    Vertex AI  : ${USE_VERTEX}"
echo ""

# ── Helper: build + deploy one service ──────────────────────────────────────
deploy_service() {
  local NAME="$1"        # Cloud Run service name
  local SRC_DIR="$2"     # path to Dockerfile directory (relative to repo root)
  local ENV_VARS="$3"    # comma-separated KEY=VALUE pairs

  echo "--> Building ${NAME}"
  gcloud builds submit "${REPO_ROOT}/${SRC_DIR}" \
    --tag "${IMAGE_BASE}/${NAME}:latest" \
    --project "${PROJECT_ID}"

  echo "--> Deploying ${NAME} to Cloud Run"
  gcloud run deploy "${NAME}" \
    --image "${IMAGE_BASE}/${NAME}:latest" \
    --region "${REGION}" \
    --platform managed \
    --service-account "${SA_EMAIL}" \
    --set-env-vars "${ENV_VARS}" \
    --allow-unauthenticated \
    --project "${PROJECT_ID}"

  local URL
  URL=$(gcloud run services describe "${NAME}" \
    --region "${REGION}" \
    --format 'value(status.url)' \
    --project "${PROJECT_ID}")
  echo "    Deployed: ${URL}"
  echo ""
}

# ── Common env vars for all services ────────────────────────────────────────
COMMON_ENV="GCP_PROJECT_ID=${PROJECT_ID},VERTEX_AI_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=${USE_VERTEX},LOG_LEVEL=INFO"

# ── Deploy services ──────────────────────────────────────────────────────────
if [[ "${SERVICE}" == "all" || "${SERVICE}" == "proxy" ]]; then
  deploy_service \
    "lore-gemini-proxy" \
    "backend/services/gemini_live_proxy" \
    "${COMMON_ENV}"
fi

if [[ "${SERVICE}" == "all" || "${SERVICE}" == "images" ]]; then
  deploy_service \
    "lore-nano-illustrator" \
    "backend/services/nano_illustrator" \
    "${COMMON_ENV},GEMINI_IMAGE_MODEL=gemini-3.1-flash-image-preview"
fi

if [[ "${SERVICE}" == "all" || "${SERVICE}" == "video" ]]; then
  deploy_service \
    "lore-veo-generator" \
    "backend/services/veo_generator" \
    "${COMMON_ENV},VEO_MODEL=veo-3.1-generate-preview"
fi

# ── Print dart-defines hint ──────────────────────────────────────────────────
if [[ "${SERVICE}" == "all" ]]; then
  PROXY_URL=$(gcloud run services describe "lore-gemini-proxy" \
    --region "${REGION}" \
    --format 'value(status.url)' \
    --project "${PROJECT_ID}" 2>/dev/null || echo "wss://lore-gemini-proxy-HASH-uc.a.run.app")

  IMAGE_URL=$(gcloud run services describe "lore-nano-illustrator" \
    --region "${REGION}" \
    --format 'value(status.url)' \
    --project "${PROJECT_ID}" 2>/dev/null || echo "https://lore-nano-illustrator-HASH-uc.a.run.app")

  VIDEO_URL=$(gcloud run services describe "lore-veo-generator" \
    --region "${REGION}" \
    --format 'value(status.url)' \
    --project "${PROJECT_ID}" 2>/dev/null || echo "https://lore-veo-generator-HASH-uc.a.run.app")

  echo "==> All services deployed."
  echo ""
  echo "Update mobile/dart-defines.json for production:"
  echo ""
  echo "  {"
  echo "    \"GEMINI_PROXY_URL\": \"${PROXY_URL/https/wss}\","
  echo "    \"NANO_ILLUSTRATOR_URL\": \"${IMAGE_URL}/generate\","
  echo "    \"VEO_GENERATOR_URL\": \"${VIDEO_URL}/generate\","
  echo "    \"GCP_PROJECT_ID\": \"${PROJECT_ID}\","
  echo "    \"GOOGLE_MAPS_API_KEY\": \"<your-maps-api-key>\","
  echo "    \"GOOGLE_GENAI_USE_VERTEXAI\": \"${USE_VERTEX}\""
  echo "  }"
  echo ""
  echo "Then rebuild the app:"
  echo "  cd mobile && flutter build apk --dart-define-from-file=dart-defines.json"
  echo ""
fi
