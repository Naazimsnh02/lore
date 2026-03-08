# Service accounts and IAM bindings for LORE services.
# Principle of least privilege: each service account gets only the roles
# it needs.

# ──────────────────────────────────────────────
# 1. WebSocket Gateway (Cloud Run)
# ──────────────────────────────────────────────
resource "google_service_account" "gateway" {
  account_id   = "lore-gateway"
  display_name = "LORE WebSocket Gateway"
  description  = "Service account for the Cloud Run WebSocket gateway service"

  depends_on = [google_project_service.apis]
}

# Gateway needs to publish messages to Pub/Sub and read from Firestore
resource "google_project_iam_member" "gateway_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_project_iam_member" "gateway_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_project_iam_member" "gateway_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_project_iam_member" "gateway_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_project_iam_member" "gateway_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

# ──────────────────────────────────────────────
# 2. Orchestrator / ADK Agent
# ──────────────────────────────────────────────
resource "google_service_account" "orchestrator" {
  account_id   = "lore-orchestrator"
  display_name = "LORE ADK Orchestrator"
  description  = "Service account for the ADK multi-agent orchestration service"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "orchestrator_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_pubsub_editor" {
  project = var.project_id
  role    = "roles/pubsub.editor"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_storage_object_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

# ──────────────────────────────────────────────
# 3. Media Worker (Veo + Nano Illustrator)
# ──────────────────────────────────────────────
resource "google_service_account" "media_worker" {
  account_id   = "lore-media-worker"
  display_name = "LORE Media Worker"
  description  = "Service account for Veo video and Nano Illustrator image generation"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "media_worker_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.media_worker.email}"
}

resource "google_project_iam_member" "media_worker_storage_object_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.media_worker.email}"
}

resource "google_project_iam_member" "media_worker_pubsub_subscriber" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_service_account.media_worker.email}"
}

resource "google_project_iam_member" "media_worker_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.media_worker.email}"
}
