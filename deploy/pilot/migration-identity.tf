resource "google_service_account" "migration" {
  account_id   = "nvb-pilot-migration"
  display_name = "NVB controlled schema migration job"
}
resource "google_project_iam_member" "migration_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.migration.email}"
}
resource "google_secret_manager_secret" "migration_database" {
  secret_id = "nvb-pilot-migration-database-url"
  replication {
    user_managed {
      replicas { location = local.primary }
      replicas { location = local.recovery }
    }
  }
  depends_on = [google_project_service.api]
}
resource "google_secret_manager_secret_iam_member" "migration_database" {
  secret_id = google_secret_manager_secret.migration_database.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migration.email}"
}
resource "google_secret_manager_secret_iam_member" "migration_session" {
  secret_id = google_secret_manager_secret.secret["nvb-pilot-session-key"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migration.email}"
}
