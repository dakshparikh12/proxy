# === Cloud Run (control-plane) ===  The ONE service (SPEC §9). It serves the
# webhook/orchestrator, the connect page + API, the authenticated /m surface, the
# Output-Media page, the WS gateway, and — the sandbox→host spine — the
# POST /meetings/{id}/relay endpoint (services/control-plane/src/control_plane/relay.py).
# The container self-migrates on boot (alembic upgrade head under an advisory lock)
# then serves `python -m control_plane.server` on $PORT.
#
# min_instances >= 1 keeps the periodic webhook-drain loop and the WS gateway warm
# (SPEC §4 latency lever + §8 warm start), so the first ask never eats a cold start.
#
# Secrets: EVERY secret is injected via value_source.secret_key_ref from Secret
# Manager (never a literal env value) — the CLAUDE.md hard rule. Non-secret config
# (project id, region, bucket NAME, the public URLs) is plain env.

resource "google_cloud_run_v2_service" "control_plane" {
  name     = var.service_name
  location = var.region

  # v0: no request-based scale-to-zero — we hold min_instances warm instead.
  deletion_protection = false

  template {
    service_account = google_service_account.control_plane.email

    # A live meeting holds a long WS connection; the periodic drain + provisioner
    # run between requests. Give the request the max window and keep CPU allocated.
    timeout = "3600s"

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    # Reach Cloud SQL through the connector (the /cloudsql unix socket the DATABASE_URL
    # DSN points at). No VPC, no private-IP peering needed.
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.pg.connection_name]
      }
    }

    containers {
      image = var.image

      # CPU always allocated so the background webhook-drain loop keeps running
      # between requests (SPEC §4/§8: warm, never cold on the first ask).
      resources {
        cpu_idle          = false
        startup_cpu_boost = true
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      ports {
        container_port = 8080
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # ---- non-secret config (plain env) --------------------------------------
      env {
        name  = "PROXY_ENV"
        value = "prod" # turns on the prod-only boot gates (SESSION_SECRET, GCP_PROJECT_ID)
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.notes.name
      }
      env {
        name  = "RECALL_REGION"
        value = var.recall_region
      }
      # The sandbox→host relay origin (SPEC §5): the service's OWN public URL. A
      # resource cannot reference its own computed .uri (that is a Terraform
      # self-cycle), so this is a TWO-PHASE apply:
      #   1. first apply with public_base_url = "" — the three URL envs below are
      #      simply not set. The code honest-degrades (relay.py:relay_url_for
      #      returns "" ⇒ the agent falls back to result-text speak; the bot joins
      #      with no live-transcript webhook until phase 2). The service comes up
      #      and Terraform now knows its URL (see outputs.tf: service_url).
      #   2. re-apply with:
      #        terraform apply -var="image=<ref>" \
      #          -var="public_base_url=$(terraform output -raw service_url)"
      #      which wires PUBLIC_BASE_URL + the two Recall deploy facts to the real
      #      origin. (See deploy/README.md — this is one extra command, no GKE, no
      #      orchestration.)
      dynamic "env" {
        for_each = var.public_base_url == "" ? {} : {
          "PUBLIC_BASE_URL"         = var.public_base_url
          "RECALL_WEBHOOK_URL"      = "${var.public_base_url}/webhooks/recall"
          "RECALL_OUTPUT_MEDIA_URL" = "${var.public_base_url}/output-media/{meeting_id}"
        }
        content {
          name  = env.key
          value = env.value
        }
      }

      # ---- secrets (Secret Manager -> env; NEVER a literal) --------------------
      # Every id below is a secret declared in secrets.tf; the app reads them by
      # these exact env names (verified against the code).
      dynamic "env" {
        for_each = local.cloud_run_secret_env
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
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.generated,
  ]
}

locals {
  # env name -> Secret Manager secret id. Both are identical here (the app reads the
  # env under the same name as the secret), but keeping the map explicit documents
  # the wiring and lets a rename diverge safely later.
  cloud_run_secret_env = {
    # hard boot gates
    "DATABASE_URL"     = google_secret_manager_secret.database_url.secret_id
    "RECALL_API_KEY"   = "RECALL_API_KEY"
    "AES_KEY_RECALL"   = "AES_KEY_RECALL"
    "AES_KEY_STT"      = "AES_KEY_STT"
    "AES_KEY_CALENDAR" = "AES_KEY_CALENDAR"
    # Claude auth — control-plane side (prod: the cloud API key)
    "ANTHROPIC_API_KEY" = "ANTHROPIC_API_KEY"
    # workroom Claude auth — the subscription token carried into the sandbox (dev)
    "CLAUDE_CODE_OAUTH_TOKEN" = "CLAUDE_CODE_OAUTH_TOKEN"
    # transport / vendor
    "RECALL_WEBHOOK_SECRET" = "RECALL_WEBHOOK_SECRET"
    "CARTESIA_API_KEY"      = "CARTESIA_API_KEY"
    "ASSEMBLYAI_API_KEY"    = "ASSEMBLYAI_API_KEY"
    "E2B_API_KEY"           = "E2B_API_KEY"
    # sessions / trust plane
    "SESSION_SECRET"           = "SESSION_SECRET"
    "SESSION_SIGNING_KEY"      = "SESSION_SIGNING_KEY"
    "PROXY_INTERNAL_TOKEN"     = "PROXY_INTERNAL_TOKEN"
    "INTERNAL_RECONCILE_TOKEN" = "INTERNAL_RECONCILE_TOKEN"
    # user auth / GitHub
    "GOOGLE_CLIENT_ID"      = "GOOGLE_CLIENT_ID"
    "GOOGLE_CLIENT_SECRET"  = "GOOGLE_CLIENT_SECRET"
    "GITHUB_WEBHOOK_SECRET" = "GITHUB_WEBHOOK_SECRET"
  }
}

# Public invoker (v0): Recall status webhooks, the Output-Media page Recall's
# headless browser fetches, and the Google OIDC callback are all inbound public
# HTTPS, so the service allows unauthenticated invocation. Route-level auth
# (session cookie / per-meeting bearer / HMAC) is enforced INSIDE the app.
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.control_plane.name
  location = google_cloud_run_v2_service.control_plane.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
