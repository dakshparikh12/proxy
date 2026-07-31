# Cloud SQL Postgres 15 — the ONE durable relational substrate (SPEC §9). Small
# tier by default; reached from Cloud Run over the Cloud SQL connector (a unix
# socket at /cloudsql/<connection_name>), so the app DSN carries no app-side SSL.
#
# The app DSN itself lives in Secret Manager (secrets.tf builds it from the
# instance connection name + the generated password), NEVER as a Terraform literal
# with the password inline.

resource "google_sql_database_instance" "pg" {
  name             = "proxy-pg"
  database_version = "POSTGRES_15"
  region           = var.region

  # Simplest safe posture for v0: a mistaken `terraform destroy` should NOT be able
  # to silently delete the database instance. Flip to false only for a deliberate teardown.
  deletion_protection = true

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # single-zone (SPEC §9: no HA/multi-region for v0)

    ip_configuration {
      # Public IP is DISABLED; Cloud Run reaches it through the Cloud SQL connector
      # (the /cloudsql unix socket), which needs no VPC and no private-IP peering.
      ipv4_enabled = false
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
    }
  }

  depends_on = [google_project_service.enabled]
}

# The logical application database. Data-bearing: a `terraform apply`/`destroy`
# must never drop it.
resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.pg.name

  lifecycle {
    prevent_destroy = true
  }
}

# A random password for the application user, generated at apply and stored ONLY in
# Secret Manager (as part of the DATABASE_URL secret) — never emitted as a plan
# literal or an output.
resource "random_password" "db" {
  length  = 32
  special = false # keep it URL-safe for the DSN
}

resource "google_sql_user" "app" {
  name     = var.db_user
  instance = google_sql_database_instance.pg.name
  password = random_password.db.result
}
