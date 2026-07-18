# === code_intel === one stateful GCE host holding the per-tenant clone / index /
# graph on a KMS-encrypted Persistent Disk. Its internal API is scoped by
# (tenant, sha): every request names the tenant and the commit sha it serves.

resource "google_kms_key_ring" "code_intel" {
  name     = "code-intel"
  location = var.region
}

resource "google_kms_crypto_key" "code_intel_disk" {
  name     = "code-intel-disk"
  key_ring = google_kms_key_ring.code_intel.id
}

# Per-tenant encrypted volume (Persistent Disk) — the crypto-shred floor.
resource "google_compute_disk" "code_intel_tenant" {
  name = "code-intel-tenant-disk"
  zone = "us-central1-a"
  size = 200

  disk_encryption_key {
    kms_key_self_link = google_kms_crypto_key.code_intel_disk.id
  }
}

resource "google_compute_instance" "code_intel" {
  name         = "code-intel"
  machine_type = "e2-standard-4"
  zone         = "us-central1-a"

  # Ephemeral boot disk; durable state lives on the attached encrypted volume.
  boot_disk {
    initialize_params {
      image = "projects/debian-cloud/global/images/family/debian-12"
    }
  }

  # Attach the KMS-encrypted per-tenant Persistent Disk.
  attached_disk {
    source = google_compute_disk.code_intel_tenant.id
    disk_encryption_key {
      kms_key_self_link = google_kms_crypto_key.code_intel_disk.id
    }
  }

  network_interface {
    network = google_compute_network.core.id
  }

  metadata = {
    # Internal API is tenant + sha scoped: every clone/index/graph read is keyed
    # by the tenant and the commit sha it belongs to.
    api_scope = "tenant+sha"
    api_path  = "/code_intel/{tenant}/{sha}/answer"
  }
}
