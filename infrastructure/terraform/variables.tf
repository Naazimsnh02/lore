variable "project_id" {
  description = "GCP project ID (must already exist with billing enabled)"
  type        = string
}

variable "region" {
  description = "Primary GCP region for Cloud Run and most services"
  type        = string
  default     = "us-central1"
}

variable "firestore_location" {
  description = "Firestore multi-region location (nam5 = US, eur3 = Europe)"
  type        = string
  default     = "nam5"
}

variable "media_bucket_location" {
  description = "Cloud Storage bucket location for media files"
  type        = string
  default     = "US"
}

variable "media_retention_days" {
  description = "Number of days to retain media files before deletion"
  type        = number
  default     = 90
}

variable "log_retention_days" {
  description = "Number of days to retain Cloud Logging log sinks in BigQuery"
  type        = number
  default     = 30
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "alert_email" {
  description = "Email address for Cloud Monitoring alerts (error rate, quota)"
  type        = string
}
