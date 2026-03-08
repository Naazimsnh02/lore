# Enable all required GCP APIs for LORE.
# Each resource corresponds to a service; Terraform will wait for activation
# before dependent resources are created (via implicit dependency on
# google_project_service outputs).

locals {
  required_apis = [
    "run.googleapis.com",                 # Cloud Run (WebSocket gateway)
    "aiplatform.googleapis.com",          # Vertex AI (Veo, Gemini models)
    "firestore.googleapis.com",           # Firestore (session memory)
    "storage.googleapis.com",             # Cloud Storage (media files)
    "pubsub.googleapis.com",              # Cloud Pub/Sub (async messaging)
    "identitytoolkit.googleapis.com",     # Identity Platform (authentication)
    "logging.googleapis.com",             # Cloud Logging
    "monitoring.googleapis.com",          # Cloud Monitoring
    "cloudtrace.googleapis.com",          # Cloud Trace (distributed tracing)
    "iam.googleapis.com",                 # IAM API
    "secretmanager.googleapis.com",       # Secret Manager (API keys, creds)
    "places.googleapis.com",              # Google Places API (location recognition)
    "maps-backend.googleapis.com",        # Google Maps Platform
    "cloudresourcemanager.googleapis.com", # Resource Manager (needed by TF)
    "compute.googleapis.com",             # Compute (Cloud Run dependency)
    "containerregistry.googleapis.com",   # Container Registry (Docker images)
    "artifactregistry.googleapis.com",    # Artifact Registry (preferred over GCR)
    "cloudbuild.googleapis.com",          # Cloud Build (CI/CD)
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project = var.project_id
  service = each.value

  # Don't disable the API when Terraform destroys this resource — other
  # resources outside Terraform may depend on it.
  disable_on_destroy = false
}
