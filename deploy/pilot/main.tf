terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source = "hashicorp/google", version = "~> 6.0"
    }
  }
}
provider "google" {
  project = var.project_id
}
variable "project_id" {
  type = string
}
variable "billing_account" {
  type = string
}
variable "image" {
  type = string
}
variable "enable_runtime" {
  default = false
}
variable "enable_dispatch" {
  default = false
}
variable "oidc_issuer" {
  type = string
}
variable "oidc_client_id" {
  type = string
}
variable "oidc_authorization_endpoint" {
  type = string
}
variable "oidc_token_endpoint" {
  type = string
}
variable "oidc_jwks_uri" {
  type = string
}
variable "oidc_redirect_uri" {
  type = string
}
variable "public_intake" {
  default = false
}
variable "monthly_budget_inr" {
  default = 150000
}
locals {
  primary  = "asia-south1"
  recovery = "asia-south2"
  apis     = toset(["run.googleapis.com", "sqladmin.googleapis.com", "compute.googleapis.com", "servicenetworking.googleapis.com", "secretmanager.googleapis.com", "cloudtasks.googleapis.com", "cloudscheduler.googleapis.com", "bigquery.googleapis.com", "aiplatform.googleapis.com", "speech.googleapis.com", "storage.googleapis.com", "billingbudgets.googleapis.com"])
}
resource "google_project_service" "api" {
  for_each           = local.apis
  service            = each.key
  disable_on_destroy = false
}
resource "google_compute_network" "pilot" {
  name                    = "nvb-pilot"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.api]
}
resource "google_compute_subnetwork" "region" {
  for_each = {
    asia-south1 = "10.40.0.0/24", asia-south2 = "10.40.1.0/24"
  }
  name                     = "nvb-${each.key}"
  region                   = each.key
  network                  = google_compute_network.pilot.id
  ip_cidr_range            = each.value
  private_ip_google_access = true
}
resource "google_compute_global_address" "private_services" {
  name          = "nvb-sql-private"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.pilot.id
}
resource "google_service_networking_connection" "private" {
  network                 = google_compute_network.pilot.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}
resource "google_sql_database_instance" "primary" {
  name                = "nvb-pilot-mumbai"
  database_version    = "POSTGRES_16"
  region              = local.primary
  deletion_protection = true
  depends_on          = [google_service_networking_connection.private]
  settings {
    tier                  = "db-custom-2-7680"
    availability_type     = "REGIONAL"
    disk_size             = 30
    disk_autoresize       = true
    disk_autoresize_limit = 100
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.pilot.id
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      location                       = local.primary
      backup_retention_settings {
        retained_backups = 14
      }
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_sql_database" "pilot" {
  name     = "nvb_pilot"
  instance = google_sql_database_instance.primary.name
}
resource "google_sql_database_instance" "recovery" {
  name                 = "nvb-pilot-delhi"
  database_version     = "POSTGRES_16"
  region               = local.recovery
  master_instance_name = google_sql_database_instance.primary.name
  deletion_protection  = true
  settings {
    tier                  = "db-custom-2-7680"
    availability_type     = "ZONAL"
    disk_size             = 30
    disk_autoresize       = true
    disk_autoresize_limit = 100
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.pilot.id
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_service_account" "runtime" {
  account_id   = "nvb-pilot-runtime"
  display_name = "NVB pilot application"
}
resource "google_service_account" "dispatch" {
  account_id   = "nvb-pilot-tasks"
  display_name = "NVB authenticated task caller"
}
resource "google_project_iam_member" "runtime" {
  for_each = toset(["roles/cloudsql.client", "roles/cloudtasks.enqueuer", "roles/aiplatform.user", "roles/speech.client", "roles/bigquery.jobUser"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_service_account_iam_member" "task_identity" {
  service_account_id = google_service_account.dispatch.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_secret_manager_secret" "secret" {
  for_each  = toset(["nvb-pilot-database-url", "nvb-pilot-session-key", "nvb-pilot-oidc-client-secret", "nvb-pilot-recovery-database-url"])
  secret_id = each.key
  replication {
    user_managed {
      replicas {
        location = local.primary
      }
      replicas {
        location = local.recovery
      }
    }
  }
  depends_on = [google_project_service.api]
}
resource "google_secret_manager_secret_iam_member" "read" {
  for_each  = google_secret_manager_secret.secret
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_bigquery_dataset" "pilot" {
  dataset_id                  = "nvb_pilot"
  location                    = local.primary
  default_table_expiration_ms = 15552000000
  delete_contents_on_destroy  = false
  depends_on                  = [google_project_service.api]
}
resource "google_bigquery_dataset_iam_member" "writer" {
  dataset_id = google_bigquery_dataset.pilot.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_bigquery_table" "reports" {
  dataset_id          = google_bigquery_dataset.pilot.dataset_id
  table_id            = "reports"
  deletion_protection = true
  schema = jsonencode([
    {
      name = "request_id", type = "STRING", mode = "REQUIRED"
    }
    ,
    {
      name = "record_version", type = "INTEGER", mode = "REQUIRED"
    }
    ,
    {
      name = "payload_json", type = "STRING", mode = "REQUIRED"
    }
    ,
    {
      name = "replicated_at", type = "TIMESTAMP", mode = "REQUIRED"
    }
  ])
  clustering = ["request_id"]
}
resource "google_cloud_tasks_queue" "pilot" {
  name     = "nvb-pilot"
  location = local.primary
  rate_limits {
    max_dispatches_per_second = 2
    max_concurrent_dispatches = 2
  }
  retry_config {
    max_attempts  = 5
    min_backoff   = "10s"
    max_backoff   = "300s"
    max_doublings = 4
  }
  depends_on = [google_project_service.api]
}
resource "google_billing_budget" "pilot" {
  billing_account = var.billing_account
  display_name    = "NVB pilot planning envelope; alerts do not cap spending"
  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }
  amount {
    specified_amount {
      currency_code = "INR"
      units         = tostring(var.monthly_budget_inr)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.8
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}
data "google_project" "current" {
  project_id = var.project_id
}
locals {
  base_env = {
    PILOT_AUDIO_BUCKET           = google_storage_bucket.audio["asia-south1"].name
    PILOT_AUDIO_RECOVERY_BUCKET  = google_storage_bucket.audio["asia-south2"].name
    DEMO_MODE                    = "false", SEED_DEMO_DATA = "false", AUTO_MIGRATE = "false",
    PILOT_ONLY                   = "true", PILOT_ID = "vellore-tirupati-water", LOCAL_EVALUATION_WORKER = "false",
    PILOT_MODEL_CALLS            = "false", PILOT_ANALYTICS_SYNC = "false", USE_REAL_GOOGLE_AI = "false",
    USE_VERTEX_FOR_GEMINI        = "true", VERTEX_PROJECT_ID = var.project_id,
    VERTEX_LOCATION              = local.primary, VERTEX_OPENAI_LOCATION = local.primary,
    BIGQUERY_LOCATION            = local.primary, PILOT_BIGQUERY_TABLE = "${var.project_id}.nvb_pilot.reports",
    PILOT_TASK_QUEUE             = google_cloud_tasks_queue.pilot.id,
    PILOT_WORKER_SERVICE_ACCOUNT = google_service_account.dispatch.email,
    OIDC_ISSUER                  = var.oidc_issuer, OIDC_CLIENT_ID = var.oidc_client_id,
    OIDC_AUTHORIZATION_ENDPOINT  = var.oidc_authorization_endpoint, OIDC_TOKEN_ENDPOINT = var.oidc_token_endpoint,
    OIDC_JWKS_URI                = var.oidc_jwks_uri, OIDC_REDIRECT_URI = var.oidc_redirect_uri
  }
  secret_env = {
    DATABASE_URL = "nvb-pilot-database-url", SECRET_KEY = "nvb-pilot-session-key", OIDC_CLIENT_SECRET = "nvb-pilot-oidc-client-secret"
  }
}
# Job exists before web runtime; execute once, then bootstrap/provision identities.
resource "google_cloud_run_v2_job" "migrate" {
  name     = "nvb-pilot-migrate"
  location = local.primary
  template {
    template {
      service_account = google_service_account.migration.email
      max_retries     = 0
      timeout         = "900s"
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.pilot.name
          subnetwork = google_compute_subnetwork.region[local.primary].name
        }
      }
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.primary.connection_name]
        }
      }
      containers {
        image   = var.image
        command = ["python", "-m", "flask", "--app", "app", "pilot-migrate"]
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
        dynamic "env" {
          for_each = local.base_env
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = { DATABASE_URL = google_secret_manager_secret.migration_database.secret_id, SECRET_KEY = "nvb-pilot-session-key" }
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.migration_database, google_secret_manager_secret_iam_member.migration_session, google_project_iam_member.migration_sql]
}
resource "google_cloud_run_v2_service" "worker" {
  count    = var.enable_runtime ? 1 : 0
  name     = "nvb-pilot-worker"
  location = local.primary
  ingress  = "INGRESS_TRAFFIC_ALL"
  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    max_instance_request_concurrency = 2
    timeout                          = "600s"
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.pilot.name
        subnetwork = google_compute_subnetwork.region[local.primary].name
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.primary.connection_name]
      }
    }
    containers {
      image = var.image
      resources {
        limits = {
          cpu = "1", memory = "1Gi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      dynamic "env" {
        for_each = local.base_env
        content {
          name  = env.key
          value = env.value
        }
      }
      # Custom audience is stable before the service URL is known.
      env {
        name  = "PILOT_WORKER_AUDIENCE"
        value = "https://nvb-pilot-worker.internal"
      }
      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/health/ready"
        }
      }
      liveness_probe {
        http_get {
          path = "/health/live"
        }
      }
    }
  }
  custom_audiences = ["https://nvb-pilot-worker.internal"]
  depends_on       = [google_cloud_run_v2_job.migrate]
}
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  count    = var.enable_runtime ? 1 : 0
  location = local.primary
  name     = google_cloud_run_v2_service.worker[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.dispatch.email}"
}
resource "google_cloud_run_v2_service" "web" {
  count            = var.enable_runtime ? 1 : 0
  name             = "nvb-pilot-web"
  location         = local.primary
  custom_audiences = ["https://nvb-pilot-worker.internal"]
  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    max_instance_request_concurrency = 16
    timeout                          = "120s"
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.pilot.name
        subnetwork = google_compute_subnetwork.region[local.primary].name
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.primary.connection_name]
      }
    }
    containers {
      image = var.image
      resources {
        limits = {
          cpu = "1", memory = "1Gi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      dynamic "env" {
        for_each = merge(local.base_env, {
          PILOT_WORKER_URL = google_cloud_run_v2_service.worker[0].uri, PILOT_WORKER_AUDIENCE = "https://nvb-pilot-worker.internal"
          }
        )
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/health/ready"
        }
      }
      liveness_probe {
        http_get {
          path = "/health/live"
        }
      }
    }
  }
  depends_on = [google_cloud_run_v2_job.migrate]
}
resource "google_cloud_run_v2_service_iam_member" "web_public" {
  count    = var.enable_runtime && var.public_intake ? 1 : 0
  location = local.primary
  name     = google_cloud_run_v2_service.web[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
# Authenticated scheduler calls web dispatcher; dispatcher creates private worker tasks.
resource "google_cloud_run_v2_service_iam_member" "dispatcher_invoker" {
  count    = var.enable_runtime ? 1 : 0
  location = local.primary
  name     = google_cloud_run_v2_service.web[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.dispatch.email}"
}
resource "google_cloud_scheduler_job" "dispatch" {
  count    = var.enable_runtime && var.enable_dispatch ? 1 : 0
  name     = "nvb-pilot-outbox"
  region   = local.primary
  schedule = "* * * * *"
  http_target {
    uri         = "${google_cloud_run_v2_service.web[0].uri}/api/v2/pilot/internal/dispatch"
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.dispatch.email
      audience              = "https://nvb-pilot-worker.internal"
    }
  }
}
output "primary_sql" {
  value = google_sql_database_instance.primary.connection_name
}
output "recovery_sql" {
  value = google_sql_database_instance.recovery.connection_name
}
output "web_url" {
  value = try(google_cloud_run_v2_service.web[0].uri, "Runtime disabled until migration")
}
output "migration_job" {
  value = google_cloud_run_v2_job.migrate.name
}
