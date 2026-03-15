# deploy-landing.ps1 — Build and deploy LORE's landing page to Cloud Run (Windows/PowerShell)

param (
    [string]$ProjectId = "",
    [string]$Region = "us-central1"
)

if (-not $ProjectId) {
    Write-Error "ERROR: -ProjectId <PROJECT_ID> is required"
    exit 1
}

$Name = "lore-landing-page"
$Image = "gcr.io/${ProjectId}/${Name}:latest"

Write-Host "==> Deploying LORE Landing Page" -ForegroundColor Cyan
Write-Host "    Project: $ProjectId"
Write-Host "    Region : $Region"
Write-Host "    Image  : $Image"
Write-Host ""

# Build
Write-Host "==> Submitting build to Cloud Build..." -ForegroundColor Yellow
gcloud builds submit "$PSScriptRoot" `
  --tag $Image `
  --project $ProjectId

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit $LASTEXITCODE
}

# Deploy
Write-Host "==> Deploying to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy $Name `
  --image $Image `
  --region $Region `
  --platform managed `
  --allow-unauthenticated `
  --project $ProjectId

if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment failed"
    exit $LASTEXITCODE
}

$Url = gcloud run services describe $Name --region $Region --format 'value(status.url)' --project $ProjectId
Write-Host ""
Write-Host "==> Landing page deployed: $Url" -ForegroundColor Green
