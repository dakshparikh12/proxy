# === control_plane === Cloud Run v2 service (autoscaling).
# Mounts the full public + internal route surface: webhook receiver, the connect
# page + API, the authenticated /m/{meeting_id} surface, the /internal/{reconcile,
# notes} endpoints, and the WS (websocket) gateway.

locals {
  # Route surface mounted by control_plane (superset of the required set).
  control_plane_routes = [
    "/webhook",              # webhook receiver
    "/connect",              # connect page + API
    "/m/{meeting_id}",       # authenticated meeting surface
    "/internal/reconcile",   # /internal/reconcile
    "/internal/notes",       # /internal/notes
    "/ws",                   # websocket gateway (ws_gateway)
  ]
}

resource "google_cloud_run_v2_service" "control_plane" {
  name     = "control-plane"
  location = var.region

  template {
    # Background provisioning must keep running between requests: CPU is always
    # allocated (--no-cpu-throttling) and the wake turn budget is one hour.
    timeout_seconds = 3600

    # Live tier keeps at least one warm instance so the ws_gateway / websocket
    # fan-out and background provisioning never cold-start.
    scaling {
      min_instance_count = 1
      max_instance_count = 20
    }

    # Direct-VPC egress to reach the private Cloud SQL IP and code_intel host.
    vpc_access {
      network    = google_compute_network.core.id
      subnetwork = google_compute_subnetwork.core.id
      egress     = "ALL_TRAFFIC"
    }

    annotations = {
      "run.googleapis.com/cpu-throttling"     = "false"
      "run.googleapis.com/cloudsql-instances" = google_sql_database_instance.prod.connection_name
      "run.googleapis.com/vpc-access-egress"  = "all-traffic"
    }

    containers {
      image = "gcr.io/${var.project_id}/control-plane:latest"

      # App DSN is a Cloud SQL Auth Proxy unix socket; the proxy terminates TLS,
      # so the DSN carries no app-side SSL params.
      env {
        name  = "DATABASE_URL"
        value = "postgresql://app@/proxy?host=/cloudsql/${google_sql_database_instance.prod.connection_name}"
      }
      env {
        name  = "ROUTE_SURFACE"
        value = "webhook,connect,/m/{meeting_id},/internal/reconcile,/internal/notes,ws_gateway"
      }
    }
  }
}
