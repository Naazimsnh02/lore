# Cloud Pub/Sub topics and subscriptions for async agent messaging.
# Requirements: 21.6 — Orchestrator uses Pub/Sub for async decoupling of
# slow generation (Veo: 15-30s) from fast narration (<400ms).

locals {
  # Topic names match task queue names referenced in orchestrator code
  pubsub_topics = {
    "narration-tasks"    = "Narration generation tasks for Gemini Live API"
    "video-tasks"        = "Video generation tasks for Veo 3.1"
    "illustration-tasks" = "Illustration generation tasks for Nano Illustrator"
    "search-tasks"       = "Fact verification tasks for Search Grounder"
    "media-ready"        = "Notification when video/illustration generation completes"
    "session-events"     = "Session lifecycle events (create, update, delete)"
  }
}

resource "google_pubsub_topic" "topics" {
  for_each = local.pubsub_topics

  name    = each.key
  project = var.project_id

  # 7-day message retention — allows replaying failed tasks
  message_retention_duration = "604800s"

  labels = {
    environment = var.environment
    service     = "lore-orchestrator"
  }

  depends_on = [google_project_service.apis]
}

# Dead-letter topic — receives messages that fail delivery after max_delivery_attempts
resource "google_pubsub_topic" "dead_letter" {
  name    = "lore-dead-letter"
  project = var.project_id

  message_retention_duration = "604800s"

  labels = {
    environment = var.environment
    purpose     = "dead-letter"
  }

  depends_on = [google_project_service.apis]
}

# ──────────────────────────────────────────────
# Subscriptions — one pull subscription per topic for the orchestrator.
# Additional push subscriptions can be added per-service via separate TF modules.
# ──────────────────────────────────────────────

resource "google_pubsub_subscription" "orchestrator_subs" {
  for_each = local.pubsub_topics

  name    = "${each.key}-orchestrator-sub"
  topic   = google_pubsub_topic.topics[each.key].name
  project = var.project_id

  # 600s ack deadline — Veo generation takes up to 60s so give workers headroom
  ack_deadline_seconds = 600

  # Retain unacknowledged messages for 7 days
  message_retention_duration = "604800s"

  # Retry policy with exponential backoff (Req 30.6)
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  # After 5 failed delivery attempts, route to dead-letter topic (Req 21.5)
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  labels = {
    environment = var.environment
    service     = "lore-orchestrator"
  }
}

# ──────────────────────────────────────────────
# IAM — allow Pub/Sub service account to forward to dead-letter topic
# (Required for dead-letter forwarding to work)
# ──────────────────────────────────────────────
data "google_project" "project" {
  project_id = var.project_id

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each = local.pubsub_topics

  project      = var.project_id
  subscription = google_pubsub_subscription.orchestrator_subs[each.key].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
