# === secrets === GCP Secret Manager. Terraform creates the secret *resources*
# and auto-populates database-url, session-secret, and the per-domain AES
# credential keys as random_id values. Each version sets
# lifecycle.ignore_changes=[secret_data] so out-of-band rotations (add-secret.sh
# / manual) survive a subsequent `terraform apply`. OAuth / Claude / Nango
# end-user grants are set out-of-band and are NOT stored here (Nango holds the
# end-user GitHub OAuth grant; Secret Manager holds platform credentials only).

locals {
  # Secrets Terraform auto-populates with a generated random_id value.
  generated_secrets = {
    "database-url"    = 32
    "session-secret"  = 32
    "aes-key-recall"  = 32
    "aes-key-stt"     = 32
    "aes-key-calendar" = 32
  }
}

resource "random_id" "secret" {
  for_each    = local.generated_secrets
  byte_length = each.value

  # Data-bearing credential key material: never regenerate/destroy on apply.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret" "generated" {
  for_each  = local.generated_secrets
  secret_id = each.key

  replication {
    auto {}
  }

  # Data-bearing: never destroy a live credential secret on a config change.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = local.generated_secrets
  secret      = google_secret_manager_secret.generated[each.key].id
  secret_data = random_id.secret[each.key].hex

  # Out-of-band rotations (add-secret.sh / manual) must survive `terraform apply`.
  lifecycle {
    ignore_changes = [secret_data]
  }
}

# Platform credentials set OUT-OF-BAND (guarded add-secret.sh): OAuth client
# secrets, Claude keys, the GitHub-App private key (GITHUB_APP_PRIVATE_KEY), and
# the Nango secret key. The secret *resource* is declared here; its value is
# never generated or committed.
resource "google_secret_manager_secret" "platform" {
  for_each = toset([
    "google-client-secret",
    "github-app-private-key",
    "nango-secret-key",
    "recall-api-key",
  ])
  secret_id = each.key

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }
}
