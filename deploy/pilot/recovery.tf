# Cold standby: no invoker grant or queue. Activate only after fencing and replica promotion.
resource "google_cloud_run_v2_service" "recovery_web" {
  count    = var.enable_runtime ? 1 : 0
  name     = "nvb-pilot-delhi-standby"
  location = local.recovery
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
        subnetwork = google_compute_subnetwork.region[local.recovery].name
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.recovery.connection_name]
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
        for_each = merge(local.secret_env, { DATABASE_URL = "nvb-pilot-recovery-database-url" })
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
