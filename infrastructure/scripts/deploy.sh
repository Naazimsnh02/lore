#!/usr/bin/env bash
# deploy.sh — Wrapper around `terraform apply` for LORE infrastructure.
#
# Usage:
#   ./infrastructure/scripts/deploy.sh [--destroy] [--plan-only] [--env <dev|staging|prod>]
#
# Options:
#   --destroy     Run terraform destroy instead of apply
#   --plan-only   Only generate plan, do not apply
#   --env         Environment (dev|staging|prod) — defaults to prod
#
# Prerequisites:
#   - bootstrap.sh has been run
#   - terraform.tfvars is populated
#   - gcloud CLI is authenticated

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"
DESTROY=false
PLAN_ONLY=false
ENV="prod"

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destroy)    DESTROY=true; shift ;;
    --plan-only)  PLAN_ONLY=true; shift ;;
    --env)        ENV="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

echo "==> LORE Infrastructure Deploy"
echo "    Environment : ${ENV}"
echo "    Destroy     : ${DESTROY}"
echo "    Plan only   : ${PLAN_ONLY}"
echo ""

cd "${TF_DIR}"

# Derive project ID from tfvars (needed for bucket name)
PROJECT_ID=$(grep 'project_id' terraform.tfvars 2>/dev/null | head -1 | awk -F'"' '{print $2}')
if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: Could not determine project_id from terraform.tfvars"
  exit 1
fi
STATE_BUCKET="${PROJECT_ID}-tf-state"

# ── terraform init (idempotent) ──────────────────────────────────────────────
echo "--> Running terraform init"
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -reconfigure \
  -input=false

# ── terraform validate ───────────────────────────────────────────────────────
echo "--> Validating configuration"
terraform validate

# ── terraform plan ───────────────────────────────────────────────────────────
PLAN_FILE="lore-${ENV}.tfplan"
echo "--> Generating plan: ${PLAN_FILE}"

if [[ "${DESTROY}" == "true" ]]; then
  terraform plan \
    -var="environment=${ENV}" \
    -destroy \
    -out="${PLAN_FILE}" \
    -input=false
else
  terraform plan \
    -var="environment=${ENV}" \
    -out="${PLAN_FILE}" \
    -input=false
fi

if [[ "${PLAN_ONLY}" == "true" ]]; then
  echo ""
  echo "==> Plan written to ${PLAN_FILE}. Skipping apply (--plan-only)."
  exit 0
fi

# ── terraform apply ──────────────────────────────────────────────────────────
echo "--> Applying plan"
terraform apply -input=false "${PLAN_FILE}"

echo ""
echo "==> Deploy complete!"
echo ""
echo "Resource outputs:"
terraform output -json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in data.items():
    val = v.get('value', '')
    if isinstance(val, dict):
        print(f'  {k}:')
        for kk, vv in val.items():
            print(f'    {kk}: {vv}')
    else:
        print(f'  {k}: {val}')
"
