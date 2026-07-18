# === iam === one least-privilege service account per role. No single broad SA
# is shared across deployables, and no SA is ever granted roles/owner or
# roles/editor. Each SA gets only the narrow roles its deployable needs.

# --- one service account per deployable/role ---
resource "google_service_account" "control_plane" {
  account_id   = "sa-control-plane"
  display_name = "control_plane Cloud Run runtime (least privilege)"
}

resource "google_service_account" "meeting_runtime" {
  account_id   = "sa-meeting-runtime"
  display_name = "per-meeting harness runtime (least privilege)"
}

resource "google_service_account" "code_intel" {
  account_id   = "sa-code-intel"
  display_name = "code_intel stateful host (least privilege)"
}

# --- least-privilege bindings: each SA gets only what its role needs ---

# control_plane needs Cloud SQL client access to reach the private Postgres.
resource "google_project_iam_member" "control_plane_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

# control_plane reads platform credentials from Secret Manager (accessor only).
resource "google_secret_manager_secret_iam_member" "control_plane_secrets" {
  for_each  = google_secret_manager_secret.platform
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.control_plane.email}"
}

# meeting_runtime writes meeting notes/artifacts to its buckets only.
resource "google_storage_bucket_iam_member" "meeting_runtime_notes" {
  bucket = google_storage_bucket.notes.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.meeting_runtime.email}"
}

# code_intel decrypts its per-tenant disk via the KMS crypto key (narrow scope).
resource "google_project_iam_member" "code_intel_kms" {
  project = var.project_id
  role    = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member  = "serviceAccount:${google_service_account.code_intel.email}"
}
