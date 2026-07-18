# === cloudsql === durable state on Cloud SQL Postgres 15, private IP only.
# Prod is REGIONAL (HA) with point-in-time recovery on db-custom-1-3840; dev is
# ZONAL on db-f1-micro. Both are reached from the app over the Cloud SQL Auth
# Proxy unix socket (host=/cloudsql/...), so the app DSN carries no SSL params.

resource "google_sql_database_instance" "prod" {
  name             = "proxy-pg-prod"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = "db-custom-1-3840"
    availability_type = "REGIONAL"

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.core.id
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
    }
  }
}

resource "google_sql_database_instance" "dev" {
  name             = "proxy-pg-dev"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.core.id
    }

    backup_configuration {
      enabled    = true
      start_time = "03:00"
    }
  }
}
