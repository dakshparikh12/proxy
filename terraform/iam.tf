# === IAM ===  ONE least-privilege runtime service account for the control-plane
# Cloud Run service. It gets exactly three capabilities and nothing broad:
#   - read every Secret Manager secret this stack declares (accessor only)
#   - connect to Cloud SQL via the connector
#   - read/write the notes/artifacts GCS bucket
# No roles/owner, no roles/editor.

resource "google_service_account" "control_plane" {
  account_id   = "sa-control-plane"
  display_name = "Proxy control-plane Cloud Run runtime (least privilege)"
}

# --- Secret Manager: accessor on each secret this stack owns (generated + db + external) ---
resource "google_secret_manager_secret_iam_member" "cp_generated" {
  for_each  = google_secret_manager_secret.generated
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_secret_manager_secret_iam_member" "cp_external" {
  for_each  = google_secret_manager_secret.external
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_secret_manager_secret_iam_member" "cp_database_url" {
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.control_plane.email}"
}

# --- Cloud SQL: connect over the connector (the /cloudsql unix socket) ---
resource "google_project_iam_member" "cp_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

# --- GCS: read/write ONLY the notes/artifacts bucket (not project-wide storage) ---
resource "google_storage_bucket_iam_member" "cp_notes" {
  bucket = google_storage_bucket.notes.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.control_plane.email}"
}
