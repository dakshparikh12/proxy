# === Secret Manager ===  The ONLY home for secret values (CLAUDE.md hard rule:
# "secrets only from Secret Manager, never hard-coded or logged"; enforced by the
# check-secret-bindings boot gate). Terraform declares the secret *resources* and
# populates the ones it can generate safely; the VALUES of external credentials
# (vendor API keys, Claude auth, OAuth client secret, GitHub webhook secret) are
# set OUT-OF-BAND by scripts/set-secrets.sh — never a Terraform literal, never a
# committed *.tfvars.
#
# The secret ids here are the EXACT env keys the new reactive-workroom code reads
# (verified against services/control-plane, services/in-meeting, and .env.example):
#   - hard boot gates .......... DATABASE_URL, GCS_BUCKET(*), RECALL_API_KEY,
#                                AES_KEY_RECALL, AES_KEY_STT, AES_KEY_CALENDAR,
#                                one Claude auth mode
#   - transport/vendor ......... CARTESIA_API_KEY, ASSEMBLYAI_API_KEY, E2B_API_KEY,
#                                RECALL_WEBHOOK_SECRET, RECALL_WEBHOOK_URL(*),
#                                RECALL_OUTPUT_MEDIA_URL(*)
#   - sessions/trust plane ..... SESSION_SECRET, SESSION_SIGNING_KEY,
#                                PROXY_INTERNAL_TOKEN, INTERNAL_RECONCILE_TOKEN
#   - user auth / GitHub ....... GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
#                                GITHUB_WEBHOOK_SECRET
#   - sandbox→host relay ....... PUBLIC_BASE_URL(*)
#   - workroom Claude auth ..... CLAUDE_CODE_OAUTH_TOKEN (dev subscription) or
#                                ANTHROPIC_API_KEY (prod cloud API)
# (*) non-secret config; injected as plain Cloud Run env in cloud_run.tf, NOT here.
#
# Deleted with the old services: code-intel / scribe had NO dedicated secrets, and
# the removed KMS/Nango/GITHUB_APP_* bindings from the old infra/secrets.tf are not
# carried over (the reactive-workroom code does not read them for the live path).

# ---------------------------------------------------------------------------
# 1) Secrets Terraform GENERATES (random key material). Never regenerated on a
#    later apply (prevent_destroy + ignore_changes) so a value stays stable across
#    applies and an out-of-band rotation survives.
# ---------------------------------------------------------------------------
locals {
  generated_secrets = {
    "AES_KEY_RECALL"           = 32 # AES-256-GCM per-domain credential key (Recall)
    "AES_KEY_STT"              = 32 # AES-256-GCM per-domain credential key (STT)
    "AES_KEY_CALENDAR"         = 32 # AES-256-GCM per-domain credential key (calendar)
    "SESSION_SECRET"           = 32 # signed-session cookie secret (prod hard gate)
    "SESSION_SIGNING_KEY"      = 32 # HMAC for the durable session cookie (distinct)
    "PROXY_INTERNAL_TOKEN"     = 32 # X-Internal-Token server-to-server bearer
    "INTERNAL_RECONCILE_TOKEN" = 32 # reconcile-endpoint bearer
    "GITHUB_WEBHOOK_SECRET"    = 32 # GitHub App push webhook HMAC key
  }

  # 2) Secrets whose value is set OUT-OF-BAND (vendor keys + Claude auth + OAuth).
  #    Terraform creates the empty secret RESOURCE; scripts/set-secrets.sh adds the
  #    version. Listed here so IAM + the Cloud Run secret bindings are complete.
  external_secrets = toset([
    "RECALL_API_KEY",         # recall.ai dashboard (per region)
    "RECALL_WEBHOOK_SECRET",  # recall.ai dashboard status-webhook signing secret (whsec_...)
    "CARTESIA_API_KEY",       # cartesia.ai Sonic TTS
    "ASSEMBLYAI_API_KEY",     # assemblyai.com (ALSO BYOK-pasted into Recall, per region)
    "E2B_API_KEY",            # e2b.dev per-meeting sandbox
    "GOOGLE_CLIENT_ID",       # Google OIDC web client id
    "GOOGLE_CLIENT_SECRET",   # Google OIDC web client secret
    "CLAUDE_CODE_OAUTH_TOKEN" # dev: the founder's subscription token carried into the workroom sandbox
    ,
    "ANTHROPIC_API_KEY" # prod: the cloud API key (control-plane Claude auth); one-line seam swaps CLAUDE_CODE_OAUTH_TOKEN <-> this
  ])
}

resource "random_id" "generated" {
  for_each    = local.generated_secrets
  byte_length = each.value

  lifecycle {
    prevent_destroy = true # credential key material — never regenerate on apply
  }
}

resource "google_secret_manager_secret" "generated" {
  for_each  = local.generated_secrets
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = local.generated_secrets
  secret      = google_secret_manager_secret.generated[each.key].id
  secret_data = random_id.generated[each.key].hex

  lifecycle {
    # An out-of-band rotation (scripts/set-secrets.sh adds a newer version) must
    # survive a subsequent `terraform apply`.
    ignore_changes = [secret_data]
  }
}

# ---------------------------------------------------------------------------
# 2) DATABASE_URL — built from the Cloud SQL instance connection name + the
#    generated app password. This is the ONLY place the password is written, and it
#    is written into Secret Manager, never surfaced as a plan literal or an output.
#    Cloud Run reaches Postgres via the Cloud SQL connector unix socket, so the DSN
#    uses host=/cloudsql/<connection_name> and carries no app-side SSL params.
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "database_url" {
  secret_id = "DATABASE_URL"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = format(
    "postgresql://%s:%s@/%s?host=/cloudsql/%s",
    var.db_user,
    random_password.db.result,
    var.db_name,
    google_sql_database_instance.pg.connection_name,
  )
}

# ---------------------------------------------------------------------------
# 3) External-value secret resources. Empty shells until scripts/set-secrets.sh
#    adds a version; the Cloud Run service references them at :latest.
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "external" {
  for_each  = local.external_secrets
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}
