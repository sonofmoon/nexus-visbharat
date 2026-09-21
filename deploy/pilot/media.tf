resource "google_storage_bucket" "audio" {
  for_each                    = toset(["asia-south1", "asia-south2"])
  name                        = "${var.project_id}-nvb-audio-${each.key}"
  location                    = each.key
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle_rule {
    condition { age = 30 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.api]
}
resource "google_storage_bucket_iam_member" "audio" {
  for_each = google_storage_bucket.audio
  bucket   = each.value.name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}
