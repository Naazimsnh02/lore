#!/usr/bin/env bash
# bootstrap.sh — Run ONCE before `terraform init`.
#
# What it does:
#   1. Authenticates gcloud CLI
#   2. Creates (or reuses) the Terraform remote-state bucket
#   3. Enables the minimal APIs needed for Terraform itself to run
#   4. Prints the backend config argument for `terraform init`
#
# Prerequisites:
#   - gcloud CLI installed and available in PATH
#   - You have Owner / Editor role on the GCP project
#   - The project already exists with billing enabled
#
# Usage:
#   ./infrastructure/scripts/bootstrap.sh <PROJECT_ID> [REGION]
#
# Example:
#   ./infrastructure/scripts/bootstrap.sh my-lore-project us-central1

set -euo pipefail

PROJECT_ID="${1:?Usage: bootstrap.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
STATE_BUCKET="${PROJECT_ID}-tf-state"

echo "==> Bootstrapping LORE infrastructure for project: ${PROJECT_ID}"

# ── 1. Set active project ────────────────────────────────────────────────────
echo "--> Setting gcloud project to ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}"

# ── 2. Enable Terraform prerequisite APIs ───────────────────────────────────
echo "--> Enabling prerequisite APIs (cloudresourcemanager, storage, iam)"
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}"

# ── 3. Create Terraform state bucket (idempotent) ───────────────────────────
echo "--> Creating Terraform state bucket: gs://${STATE_BUCKET}"
if gsutil ls -p "${PROJECT_ID}" "gs://${STATE_BUCKET}" &>/dev/null; then
  echo "    Bucket already exists — skipping creation."
else
  gsutil mb \
    -p "${PROJECT_ID}" \
    -l "${REGION}" \
    -b on \
    "gs://${STATE_BUCKET}"

  # Enable versioning so previous state files are recoverable
  gsutil versioning set on "gs://${STATE_BUCKET}"

  # Prevent public access
  gsutil uniformbucketlevelaccess set on "gs://${STATE_BUCKET}"
fi

# ── 4. Print next steps ──────────────────────────────────────────────────────
echo ""
echo "==> Bootstrap complete!"
echo ""
echo "Next steps:"
echo ""
echo "  1. Copy the example vars file and fill in your values:"
echo "       cp infrastructure/terraform/terraform.tfvars.example infrastructure/terraform/terraform.tfvars"
echo "       # Edit terraform.tfvars and set project_id, alert_email, etc."
echo ""
echo "  2. Initialise Terraform with the remote state bucket:"
echo "       cd infrastructure/terraform"
echo "       terraform init -backend-config=\"bucket=${STATE_BUCKET}\""
echo ""
echo "  3. Review the plan:"
echo "       terraform plan -out=lore.tfplan"
echo ""
echo "  4. Apply:"
echo "       terraform apply lore.tfplan"
echo ""
