# === host firewall layer 2: the cloud security group (AC-OBS-006) ===
# Defence in depth pairs the on-host firewall (ufw, set in deploy/harden.sh) with
# this VPC-level security group. Default-deny ingress; only the internal API and
# health probes are reachable, and only from within the core network.

resource "google_compute_firewall" "code_intel_internal_only" {
  name      = "code-intel-internal-only"
  network   = google_compute_network.core.id
  direction = "INGRESS"
  priority  = 1000

  # Allow only the tenant+SHA-scoped internal API and health checks from inside the VPC.
  allow {
    protocol = "tcp"
    ports    = ["8443", "8080"]
  }

  source_ranges = ["10.0.0.0/8"]
  target_tags   = ["code-intel"]
}

resource "google_compute_firewall" "deny_all_ingress" {
  name      = "deny-all-ingress"
  network   = google_compute_network.core.id
  direction = "INGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]
}
