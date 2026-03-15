# cleanup.ps1 — Tear down all LORE GCP resources after the hackathon.
#
# Deletes:
#   - Cloud Run services (proxy, illustrator, video generator, landing page)
#   - Container images in GCR
#   - Service account
#   - Cloud Build artifacts bucket (optional, --nuke flag)
#
# Usage:
#   .\infrastructure\scripts\cleanup.ps1 -ProjectId geminiliveagent-487800
#   .\infrastructure\scripts\cleanup.ps1 -ProjectId geminiliveagent-487800 -Nuke   # also deletes storage bucket

param (
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [switch]$Nuke = $false,   # also wipe Cloud Build bucket and GCR images
    [switch]$Yes = $false     # skip confirmation prompt
)

$ErrorActionPreference = "Stop"

$Services = @(
    "lore-gemini-proxy",
    "lore-nano-illustrator",
    "lore-veo-generator",
    "lore-landing-page"
)
$SAEmail = "lore-backend@${ProjectId}.iam.gserviceaccount.com"
$Images   = $Services | ForEach-Object { "gcr.io/${ProjectId}/$_" }

Write-Host ""
Write-Host "==> LORE Hackathon Cleanup" -ForegroundColor Red
Write-Host "    Project : $ProjectId"
Write-Host "    Region  : $Region"
Write-Host "    Nuke    : $Nuke  (deletes GCR images + Cloud Build bucket)"
Write-Host ""
Write-Host "    Services to delete:" -ForegroundColor Yellow
$Services | ForEach-Object { Write-Host "      - $_" }
Write-Host "    Service account: $SAEmail" -ForegroundColor Yellow
Write-Host ""

if (-not $Yes) {
    $confirm = Read-Host "Type 'yes' to confirm deletion of all resources"
    if ($confirm -ne "yes") {
        Write-Host "Aborted." -ForegroundColor Gray
        exit 0
    }
}

# ── Delete Cloud Run services ────────────────────────────────────────────────
Write-Host ""
Write-Host "--> Deleting Cloud Run services..." -ForegroundColor Yellow
foreach ($svc in $Services) {
    Write-Host "    Deleting $svc..." -NoNewline
    $result = gcloud run services delete $svc `
        --region $Region `
        --project $ProjectId `
        --quiet 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " done" -ForegroundColor Green
    } else {
        Write-Host " not found (skipped)" -ForegroundColor Gray
    }
}

# ── Delete GCR images ────────────────────────────────────────────────────────
if ($Nuke) {
    Write-Host ""
    Write-Host "--> Deleting container images from GCR..." -ForegroundColor Yellow
    foreach ($img in $Images) {
        Write-Host "    Deleting $img..." -NoNewline
        $result = gcloud container images delete "${img}:latest" `
            --project $ProjectId `
            --quiet `
            --force-delete-tags 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host " done" -ForegroundColor Green
        } else {
            Write-Host " not found (skipped)" -ForegroundColor Gray
        }
    }
}

# ── Delete service account ───────────────────────────────────────────────────
Write-Host ""
Write-Host "--> Deleting service account $SAEmail..." -NoNewline
$result = gcloud iam service-accounts delete $SAEmail `
    --project $ProjectId `
    --quiet 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host " done" -ForegroundColor Green
} else {
    Write-Host " not found (skipped)" -ForegroundColor Gray
}

# ── Delete Cloud Build / GCS bucket ─────────────────────────────────────────
if ($Nuke) {
    Write-Host ""
    Write-Host "--> Deleting Cloud Build artifacts bucket..." -NoNewline
    $bucket = "gs://${ProjectId}_cloudbuild"
    $result = gcloud storage rm --recursive $bucket --project $ProjectId 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " done" -ForegroundColor Green
    } else {
        Write-Host " not found (skipped)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "==> Cleanup complete." -ForegroundColor Green
if (-not $Nuke) {
    Write-Host "    Tip: run with -Nuke to also delete GCR images and the Cloud Build bucket." -ForegroundColor Gray
}
Write-Host ""
