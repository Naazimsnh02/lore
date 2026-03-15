# bootstrap.ps1 - One-time GCP project setup for LORE (Windows/PowerShell)
param (
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [string]$Region = "us-central1"
)

$ErrorActionPreference = "Stop"
$SAName = "lore-backend"
$SAEmail = "${SAName}@${ProjectId}.iam.gserviceaccount.com"

Write-Host "==> Bootstrapping LORE for project: ${ProjectId} (region: ${Region})" -ForegroundColor Cyan

# 1. Set active project
Write-Host "--> Setting gcloud project"
gcloud config set project $ProjectId

# 2. Enable required APIs
Write-Host "--> Enabling required GCP APIs"
$APIs = @(
    "run.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "places.googleapis.com",
    "maps-backend.googleapis.com",
    "maps-android-backend.googleapis.com",
    "maps-ios-backend.googleapis.com",
    "directions-backend.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com"
)

gcloud services enable ($APIs -join " ") --project=$ProjectId
Write-Host "    APIs enabled." -ForegroundColor Green

# 3. Create service account
Write-Host "--> Creating service account: $SAEmail"
$SAExists = gcloud iam service-accounts describe $SAEmail --project=$ProjectId 2>$null
if ($SAExists) {
    Write-Host "    Service account already exists - skipping creation."
} else {
    gcloud iam service-accounts create $SAName --display-name="LORE Backend" --project=$ProjectId
}

# 4. Grant required IAM roles
Write-Host "--> Granting IAM roles to $SAEmail"
$Roles = @(
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter"
)

foreach ($Role in $Roles) {
    gcloud projects add-iam-policy-binding $ProjectId `
        --member="serviceAccount:$SAEmail" `
        --role=$Role `
        --condition=None `
        --quiet > $null
}
Write-Host "    Roles granted." -ForegroundColor Green

# 5. Instructions
Write-Host ""
Write-Host "==> Bootstrap complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "  1. Store your Places API key in Secret Manager:"
Write-Host "       echo 'YOUR_PLACES_API_KEY' | gcloud secrets create lore-places-api-key --data-file=- --project=$ProjectId"
Write-Host ""
Write-Host "  2. Grant the service account access to the secret:"
Write-Host "       gcloud secrets add-iam-policy-binding lore-places-api-key --member='serviceAccount:$SAEmail' --role='roles/secretmanager.secretAccessor' --project=$ProjectId"
Write-Host ""
Write-Host "  3. Deploy the 3 Cloud Run services:"
Write-Host "       .\infrastructure\scripts\deploy.ps1 -ProjectId $ProjectId -Region $Region -Vertex"
Write-Host ""
