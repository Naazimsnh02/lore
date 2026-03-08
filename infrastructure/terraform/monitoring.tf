# Cloud Logging sinks and Cloud Monitoring alert policies.
# Requirements: 26.1–26.7 — structured logging, distributed tracing,
# error-rate alerts, 30-day retention.

# ──────────────────────────────────────────────
# Notification channel — email alerts (Req 26.4)
# ──────────────────────────────────────────────
resource "google_monitoring_notification_channel" "email" {
  display_name = "LORE Ops Email"
  type         = "email"
  project      = var.project_id

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.apis]
}

# ──────────────────────────────────────────────
# Alert: error rate > 5% on any Cloud Run service (Req 26.4)
# ──────────────────────────────────────────────
resource "google_monitoring_alert_policy" "high_error_rate" {
  display_name = "LORE High Error Rate (5xx > 5 req/min)"
  project      = var.project_id
  combiner     = "OR"

  # Alert when 5xx count exceeds 5 requests/minute — simple count threshold
  # that doesn't require boolean metric type (REDUCE_FRACTION_TRUE limitation).
  conditions {
    display_name = "Cloud Run 5xx requests > 5 per minute"

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 5

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ──────────────────────────────────────────────
# Alert: WebSocket P99 latency > 200ms (Req 20.7, 7)
# ──────────────────────────────────────────────
resource "google_monitoring_alert_policy" "websocket_latency" {
  display_name = "LORE WebSocket High Latency (P99 > 200ms)"
  project      = var.project_id
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run request latency P99 > 200ms"

    condition_threshold {
      filter      = "resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"lore-gateway\" AND metric.type=\"run.googleapis.com/request_latencies\""
      duration    = "300s"
      comparison  = "COMPARISON_GT"
      threshold_value = 200

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_MEAN"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ──────────────────────────────────────────────
# Alert: Pub/Sub dead-letter message backlog (failed tasks)
# ──────────────────────────────────────────────
resource "google_monitoring_alert_policy" "dead_letter_backlog" {
  display_name = "LORE Dead-Letter Topic Backlog > 10 messages"
  project      = var.project_id
  combiner     = "OR"

  conditions {
    display_name = "Dead-letter undelivered message count > 10"

    condition_threshold {
      filter      = "resource.type=\"pubsub_topic\" AND resource.label.topic_id=\"lore-dead-letter\" AND metric.type=\"pubsub.googleapis.com/topic/send_message_operation_count\""
      duration    = "300s"
      comparison  = "COMPARISON_GT"
      threshold_value = 10

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}

# ──────────────────────────────────────────────
# Log-based metric: count ERROR severity logs (Req 26.2)
# ──────────────────────────────────────────────
resource "google_logging_metric" "error_log_count" {
  name        = "lore_error_log_count"
  project     = var.project_id
  description = "Count of ERROR+ severity log entries across LORE services"

  filter = "severity>=ERROR AND (resource.type=\"cloud_run_revision\" OR resource.type=\"pubsub_topic\")"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    display_name = "LORE Error Log Count"
  }
}

# ──────────────────────────────────────────────
# Log sink → BigQuery for 30-day structured log retention (Req 26.6)
# ──────────────────────────────────────────────
resource "google_bigquery_dataset" "log_sink" {
  dataset_id  = "lore_logs"
  project     = var.project_id
  location    = var.region
  description = "LORE structured log archive (30-day retention)"

  # Delete tables with data when dataset is destroyed (only in dev)
  delete_contents_on_destroy = var.environment == "dev" ? true : false

  default_table_expiration_ms = var.log_retention_days * 24 * 60 * 60 * 1000

  labels = {
    environment = var.environment
    service     = "lore-logging"
  }

  depends_on = [google_project_service.apis]
}

resource "google_logging_project_sink" "bigquery_sink" {
  name        = "lore-logs-to-bigquery"
  project     = var.project_id
  destination = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.log_sink.dataset_id}"

  # Only export LORE service logs
  filter = "resource.type=\"cloud_run_revision\""

  # Use partitioned BigQuery tables for cost efficiency
  bigquery_options {
    use_partitioned_tables = true
  }

  unique_writer_identity = true
}

# Grant the log sink's writer identity permission to write to the dataset
resource "google_bigquery_dataset_iam_member" "log_sink_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.log_sink.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.bigquery_sink.writer_identity
}

# ──────────────────────────────────────────────
# Uptime check — verify gateway health endpoint every 60s
# ──────────────────────────────────────────────
resource "google_monitoring_uptime_check_config" "gateway_health" {
  display_name = "LORE Gateway Health Check"
  project      = var.project_id
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health"
    port         = "443"
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      # Populated after gateway Cloud Run service is deployed
      host = "lore-gateway-${var.project_id}.run.app"
    }
  }

  depends_on = [google_project_service.apis]
}
