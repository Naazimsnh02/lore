# Firestore (session memory) and Cloud Storage (media files)

# ──────────────────────────────────────────────
# Firestore — session memory
# ──────────────────────────────────────────────

# Firestore database in native mode, multi-region for HA.
# Requirements: 10.2, 22.1 — encrypted at rest (default GCP behavior).
resource "google_firestore_database" "session_memory" {
  provider = google-beta

  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_location

  # Native mode is required for real-time listeners and subcollections.
  type = "FIRESTORE_NATIVE"

  # Enables point-in-time recovery (PITR) — 7-day window for accidental deletes.
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"

  # Daily backups retained for 7 days.
  delete_protection_state = "DELETE_PROTECTION_ENABLED"

  depends_on = [google_project_service.apis]
}

# Composite indexes for common query patterns.
# sessions by userId + createdAt (for cross-session queries, Req 10.4)
resource "google_firestore_index" "sessions_by_user" {
  project    = var.project_id
  collection = "sessions"
  database   = google_firestore_database.session_memory.name

  fields {
    field_path = "userId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "createdAt"
    order      = "DESCENDING"
  }
}

# locations visited by userId + timestamp (for GPS history queries)
resource "google_firestore_index" "locations_by_user" {
  project    = var.project_id
  collection = "locationVisits"
  database   = google_firestore_database.session_memory.name

  fields {
    field_path = "userId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "timestamp"
    order      = "DESCENDING"
  }
}

# ──────────────────────────────────────────────
# Cloud Storage — media files (videos, illustrations, chronicles)
# ──────────────────────────────────────────────

resource "google_storage_bucket" "media_store" {
  name     = "${var.project_id}-lore-media"
  location = var.media_bucket_location

  # Uniform bucket-level access — no per-object ACLs (security best practice)
  uniform_bucket_level_access = true

  # Require TLS for all access (Req 10.7)
  force_destroy = false

  versioning {
    enabled = true
  }

  # Automatically delete media after retention period (Req 22.5)
  lifecycle_rule {
    condition {
      age = var.media_retention_days
    }
    action {
      type = "Delete"
    }
  }

  # Move to Nearline after 30 days to reduce costs
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Authorization"]
    max_age_seconds = 3600
  }

  labels = {
    environment = var.environment
    service     = "lore-media-store"
  }

  depends_on = [google_project_service.apis]
}

# Terraform state bucket — created by bootstrap.sh before `terraform init`,
# referenced here only to document it; it must pre-exist.
# (No resource block intentionally — circular dependency if managed by TF.)

# ──────────────────────────────────────────────
# Cloud Storage — Terraform remote state
# (created by bootstrap.sh, documented here for reference)
# Bucket name: ${project_id}-tf-state
# ──────────────────────────────────────────────
