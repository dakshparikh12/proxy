# Outputs — the handful of facts the deploy commands + the founder need. No secret
# VALUE is ever output (the db password lives only in the DATABASE_URL secret).

output "service_url" {
  description = "The control-plane's public HTTPS URL. Feed it back on the second apply as -var=\"public_base_url=$(terraform output -raw service_url)\" and register it in the Recall dashboard webhook + Google OIDC redirect."
  value       = google_cloud_run_v2_service.control_plane.uri
}

output "image_repo" {
  description = "The Artifact Registry Docker repo path to build+push the control-plane image into."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "cloudsql_connection_name" {
  description = "The Cloud SQL instance connection name (<project>:<region>:proxy-pg) the Cloud Run connector mounts."
  value       = google_sql_database_instance.pg.connection_name
}

output "gcs_bucket" {
  description = "The notes/artifacts bucket name the app reads as GCS_BUCKET."
  value       = google_storage_bucket.notes.name
}

output "runtime_service_account" {
  description = "The least-privilege runtime service account email the Cloud Run service runs as."
  value       = google_service_account.control_plane.email
}

output "secret_ids" {
  description = "Every Secret Manager secret this module declares. The externally-populated ones must have a version added (scripts/set-secrets.sh) before the service can boot."
  value = sort(concat(
    keys(google_secret_manager_secret.generated),
    [google_secret_manager_secret.database_url.secret_id],
    [for s in google_secret_manager_secret.external : s.secret_id],
  ))
}

output "external_secrets_to_populate" {
  description = "The secrets whose VALUES you must set out-of-band (vendor keys, Claude auth, OAuth) before boot — run scripts/set-secrets.sh."
  value       = sort([for s in google_secret_manager_secret.external : s.secret_id])
}
