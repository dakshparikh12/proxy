# === storage === GCS buckets with Object Versioning enabled. The notes and
# artifacts buckets rely on versioning for if_generation_match optimistic
# concurrency.

resource "google_storage_bucket" "notes" {
  name     = "proxy-v0-notes"
  location = "us-central1"

  versioning {
    enabled = true
  }
}

resource "google_storage_bucket" "artifacts" {
  name     = "proxy-v0-artifacts"
  location = "us-central1"

  versioning {
    enabled = true
  }
}
