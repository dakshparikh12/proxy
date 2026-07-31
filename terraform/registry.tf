# Artifact Registry — the one Docker repo the control-plane image is pushed to.
# The build step in deploy/README.md tags into
#   <region>-docker.pkg.dev/<project>/proxy/<service_name>:<tag>
# and `terraform apply -var="image=..."` deploys that exact ref.

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "proxy"
  description   = "Proxy control-plane container images."
  format        = "DOCKER"

  depends_on = [google_project_service.enabled]
}
