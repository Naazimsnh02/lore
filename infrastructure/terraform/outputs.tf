output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "Primary deployment region"
  value       = var.region
}

# Service account emails — consumed by Cloud Run service definitions in Phase 1
output "gateway_service_account" {
  description = "WebSocket Gateway service account email"
  value       = google_service_account.gateway.email
}

output "orchestrator_service_account" {
  description = "ADK Orchestrator service account email"
  value       = google_service_account.orchestrator.email
}

output "media_worker_service_account" {
  description = "Media Worker (Veo + Illustrator) service account email"
  value       = google_service_account.media_worker.email
}

# Storage
output "media_bucket_name" {
  description = "Cloud Storage bucket name for media files"
  value       = google_storage_bucket.media_store.name
}

output "media_bucket_url" {
  description = "Cloud Storage bucket URL (gs://...)"
  value       = google_storage_bucket.media_store.url
}

output "firestore_database" {
  description = "Firestore database name"
  value       = google_firestore_database.session_memory.name
}

# Pub/Sub
output "pubsub_topic_ids" {
  description = "Map of topic name → full topic ID"
  value       = { for k, v in google_pubsub_topic.topics : k => v.id }
}

output "pubsub_subscription_ids" {
  description = "Map of subscription name → full subscription ID"
  value       = { for k, v in google_pubsub_subscription.orchestrator_subs : k => v.id }
}

output "dead_letter_topic_id" {
  description = "Dead-letter Pub/Sub topic ID"
  value       = google_pubsub_topic.dead_letter.id
}

# Monitoring
output "notification_channel" {
  description = "Cloud Monitoring email notification channel ID"
  value       = google_monitoring_notification_channel.email.name
}

output "log_dataset_id" {
  description = "BigQuery dataset ID for structured log archive"
  value       = google_bigquery_dataset.log_sink.dataset_id
}
