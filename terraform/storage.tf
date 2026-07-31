# GCS — ONE bucket for finalized meeting notes + artifacts (SPEC §8/§9). The app
# reads its name from GCS_BUCKET. Object Versioning is REQUIRED: libs/http's object
# store uses if_generation_match optimistic concurrency (CLAUDE.md: GCS is the
# object-versioned durable substrate).

resource "google_storage_bucket" "notes" {
  name     = "${var.project_id}-proxy-notes"
  location = var.region

  # Object Versioning ON — the app's optimistic-concurrency writes depend on it.
  versioning {
    enabled = true
  }

  # Uniform bucket-level access (no per-object ACLs) — the modern, simplest default.
  uniform_bucket_level_access = true

  depends_on = [google_project_service.enabled]
}
