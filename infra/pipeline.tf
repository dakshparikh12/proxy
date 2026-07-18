# === pipeline === CI/CD delivery path.
#
# dev:  auto-deploys. A Cloud Build trigger fires on every push to main, builds
#       the image, pushes it to Artifact Registry, and deploys it to the dev
#       Cloud Run service automatically.
# prod: promote-only. There is NO direct push path to prod. Prod ships the exact
#       image that was already built and tested in dev, by re-tagging it in
#       Artifact Registry (immutable tags mean the tested bytes can never be
#       overwritten) and running the guarded promote job (deploy/promote.sh).
#       The promote job is the ONLY way an image reaches prod.

# Artifact Registry — the single source of truth for images. Immutable tags mean
# an exact-tested tag can never be re-pushed with different bytes, which is what
# makes promote-by-tag safe.
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "proxy-images"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true # IMMUTABLE_TAGS: an exact-tested tag can't be overwritten
  }
}

# Dev auto-deploy: Cloud Build trigger on push to main builds + deploys to dev.
resource "google_cloudbuild_trigger" "dev_autodeploy" {
  name        = "dev-autodeploy"
  location    = var.region
  description = "Auto-build and auto-deploy every push to main to the dev service."

  github {
    owner = "proxy-org"
    name  = "proxy"
    push {
      branch = "^main$"
    }
  }

  # The build config builds the image, pushes it to Artifact Registry, and
  # deploys it to the DEV Cloud Run service. Prod is untouched by this trigger.
  filename = "deploy/cloudbuild.dev.yaml"
}

# Prod promote job: the promote path re-tags the exact dev-tested image in
# Artifact Registry and rolls it out. It is guarded and manual — the promote job
# (deploy/promote.sh) is the only route to prod; see deploy/ for the job body.
# apply order per env: bootstrap -> platform (state buckets before workloads).
