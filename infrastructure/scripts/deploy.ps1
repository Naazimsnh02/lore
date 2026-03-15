# deploy.ps1 — Build and deploy LORE's 3 active Cloud Run services (Windows/PowerShell)
param (
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [ValidateSet("all", "proxy", "images", "video")][string]$Service = "all",
    [switch]$Vertex = $false
)

$ErrorActionPreference = "Stop"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$REPO_ROOT = (Get-Item $SCRIPT_DIR).Parent.Parent.FullName

$SAEmail = "lore-backend@${ProjectId}.iam.gserviceaccount.com"
$ImageBase = "gcr.io/${ProjectId}"
$UseVertex = if ($Vertex) { "true" } else { "false" }

Write-Host "==> LORE Cloud Run Deploy" -ForegroundColor Cyan
Write-Host "    Project    : ${ProjectId}"
Write-Host "    Region     : ${Region}"
Write-Host "    Service    : ${Service}"
Write-Host "    Vertex AI  : ${UseVertex}"
Write-Host ""

# -- Helper: build + deploy one service --
function Deploy-Service {
    param($Name, $SrcDir, $EnvVars)

    Write-Host "--> Building ${Name}" -ForegroundColor Yellow
    $SourcePath = Join-Path $REPO_ROOT $SrcDir
    gcloud builds submit $SourcePath `
        --tag "${ImageBase}/${Name}:latest" `
        --project $ProjectId

    Write-Host "--> Deploying ${Name} to Cloud Run" -ForegroundColor Yellow
    gcloud run deploy $Name `
        --image "${ImageBase}/${Name}:latest" `
        --region $Region `
        --platform managed `
        --service-account $SAEmail `
        --set-env-vars $EnvVars `
        --allow-unauthenticated `
        --project $ProjectId

    $URL = gcloud run services describe $Name `
        --region $Region `
        --format 'value(status.url)' `
        --project $ProjectId
    Write-Host "    Deployed: ${URL}" -ForegroundColor Green
    Write-Host ""
}

# -- Common env vars --
$CommonEnv = "GCP_PROJECT_ID=${ProjectId},VERTEX_AI_LOCATION=${Region},GOOGLE_GENAI_USE_VERTEXAI=${UseVertex},LOG_LEVEL=INFO"

# -- Deploy services --
if ($Service -eq "all" -or $Service -eq "proxy") {
    Deploy-Service -Name "lore-gemini-proxy" -SrcDir "backend/services/gemini_live_proxy" -EnvVars $CommonEnv
}

if ($Service -eq "all" -or $Service -eq "images") {
    Deploy-Service -Name "lore-nano-illustrator" -SrcDir "backend/services/nano_illustrator" -EnvVars "${CommonEnv},GEMINI_IMAGE_MODEL=gemini-3.1-flash-image-preview"
}

if ($Service -eq "all" -or $Service -eq "video") {
    Deploy-Service -Name "lore-veo-generator" -SrcDir "backend/services/veo_generator" -EnvVars "${CommonEnv},VEO_MODEL=veo-3.1-generate-preview"
}

# -- Print dart-defines hint --
if ($Service -eq "all") {
    $ProxyUrl = gcloud run services describe "lore-gemini-proxy" --region $Region --format 'value(status.url)' --project $ProjectId 2>$null
    if (!$ProxyUrl) { $ProxyUrl = "wss://lore-gemini-proxy-HASH-uc.a.run.app" }
    $ProxyUrl = $ProxyUrl -replace "https", "wss"

    $ImageUrl = gcloud run services describe "lore-nano-illustrator" --region $Region --format 'value(status.url)' --project $ProjectId 2>$null
    $VideoUrl = gcloud run services describe "lore-veo-generator" --region $Region --format 'value(status.url)' --project $ProjectId 2>$null

    Write-Host "==> All services deployed." -ForegroundColor Green
    Write-Host ""
    Write-Host "Update mobile/dart-defines.json for production:"
    Write-Host ""
    Write-Host "  {"
    Write-Host "    `"GEMINI_PROXY_URL`": `"$ProxyUrl`","
    Write-Host "    `"NANO_ILLUSTRATOR_URL`": `"${ImageUrl}/generate`","
    Write-Host "    `"VEO_GENERATOR_URL`": `"${VideoUrl}/generate`","
    Write-Host "    `"GCP_PROJECT_ID`": `"$ProjectId`","
    Write-Host "    `"GOOGLE_MAPS_API_KEY`": `"<your-maps-api-key>`","
    Write-Host "    `"GOOGLE_GENAI_USE_VERTEXAI`": `"$UseVertex`""
    Write-Host "  }"
    Write-Host ""
    Write-Host "Then rebuild the app:"
    Write-Host "  cd mobile; flutter build apk --dart-define-from-file=dart-defines.json"
}
