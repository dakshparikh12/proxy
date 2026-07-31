# Enable exactly the GCP APIs this simple stack needs — nothing more (SPEC §9:
# Cloud Run + Cloud SQL + Secret Manager + GCS + Artifact Registry; E2B is an
# external SaaS, not a GCP API). Every other resource depends on these so a fresh
# project comes up in one `terraform apply` without a manual "enable API" click.
#
# NOTE: enabling a service on a brand-new project can need Billing linked first —
# that is the founder gate called out in deploy/README.md (billing + org policy
# are the one thing Terraform cannot self-provision).

locals {
  required_services = [
    "run.googleapis.com",             # Cloud Run (the control-plane service)
    "sqladmin.googleapis.com",        # Cloud SQL Postgres
    "secretmanager.googleapis.com",   # Secret Manager (all secrets)
    "storage.googleapis.com",         # GCS (notes/artifacts bucket)
    "artifactregistry.googleapis.com" # Artifact Registry (the image)
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.required_services)
  service  = each.value

  # Keep the APIs enabled if the config is torn down (avoids breaking other
  # workloads in a shared customer project).
  disable_on_destroy = false
}
