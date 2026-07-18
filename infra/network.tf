# === network === single VPC that the three deployables share. Direct-VPC egress
# from control_plane and the private Cloud SQL IP both attach here.

resource "google_compute_network" "core" {
  name                    = "proxy-core"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "core" {
  name          = "proxy-core"
  region        = var.region
  network       = google_compute_network.core.id
  ip_cidr_range = "10.10.0.0/20"
}
