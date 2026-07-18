# === data_protection === destroy-guards for the data-bearing resources that a
# `terraform apply` (or a rogue plan) must never be able to delete: the specs
# bucket that holds the authored component specs, and a project-deletion lien
# that blocks the whole GCP project from being torn down.

# The specs bucket holds the authored component specs (the source of truth the
# pipeline builds from). Object Versioning on; prevent_destroy guards the bytes.
resource "google_storage_bucket" "specs" {
  name     = "proxy-v0-specs"
  location = "us-central1"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

# === lien === a resource-manager lien blocks project deletion outright.
resource "google_resource_manager_lien" "project_guard" {
  parent       = "projects/${var.project_id}"
  restrictions = ["resourcemanager.projects.delete"]
  origin       = "terraform-data-protection"
  reason       = "Blocks deletion of the project holding data-bearing resources."

  lifecycle {
    prevent_destroy = true
  }
}
