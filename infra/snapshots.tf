# === code_intel per-tenant volume: daily snapshot schedule (AC-OBS-010) ===
# The clone / structural index / dependency-graph SQLite on the code_intel volume
# is a REBUILDABLE derived cache — Postgres + GCS are the durable source of truth —
# but a daily snapshot bounds the rebuild window after a host loss.

resource "google_compute_resource_policy" "code_intel_daily_snapshot" {
  name   = "code-intel-daily-snapshot"
  region = "us-central1"

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "03:00"
      }
    }
    retention_policy {
      max_retention_days    = 14
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      guest_flush = false
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "code_intel_snapshot" {
  name = google_compute_resource_policy.code_intel_daily_snapshot.name
  disk = google_compute_disk.code_intel_tenant.name
  zone = "us-central1-a"
}
